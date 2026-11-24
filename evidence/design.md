# Design

## Overview

The service is a small FastAPI application backed by SQLite. A deterministic planner gathers evidence through a read-only tool gateway and proposes an action. A policy engine validates the proposal. Low-risk actions may be auto-approved; financial or higher-risk actions require a human approver. A mock executor performs the action idempotently, and every state change is written to an append-only audit log.

The agent has no execute tool. The only write path is a proposal, and the service — not the planner — decides whether it can run.

## Architecture

```text
Client (React UI / curl / demo)
        |
        v
FastAPI API (auth, request ID, validation, size limit)
        |
        v
Case Workflow (state machine, idempotency, audit)
        |
        +--> Planner (deterministic; read-only tools only)
        |         |
        |         v
        |    Tool Gateway (allowlist + schema)
        |         +--> payment.lookup
        |         +--> account.lookup
        |         +--> policy.search
        |
        +--> Policy Engine (limits, roles, separation of duties)
                 |
                 +--> auto-approved --> Mock Executor (idempotent)
                 +--> pending approval --> Approver --> Mock Executor
                 +--> rejected
                 +--> escalated (safe abstain)
        |
        v
SQLite (cases, proposals, approvals, executions, audit)
        |
        v
Append-only audit + JSON logs + /healthz /readyz /status
```

## Data flow

1. An analyst creates a case for a synthetic payment.
2. The agent calls triage. The planner reads payment/account/policy evidence through the tool gateway.
3. The planner returns a proposal with rationale and evidence references.
4. The policy engine validates the proposal and returns an outcome.
5. Auto-approved actions execute immediately. Financial/higher-risk actions wait for an approver.
6. The mock executor writes an execution record; the case moves to a terminal state.
7. Every step writes an audit event and a structured log line.

## Component boundaries

- **API**: authentication, request IDs, validation, HTTP error mapping.
- **Workflow**: state transitions, orchestration, transactions, idempotency.
- **Planner**: evidence gathering and proposal; cannot write or execute.
- **Tool gateway**: allowlist, schema validation, typed results, tool-call audit.
- **Policy engine**: pure rules; outcome + reason codes + approval requirement.
- **Executor**: protocol; mock implementation; idempotent by proposal.
- **Repository**: parameterized SQL, constraints, foreign keys, WAL mode.
- **Audit**: append-only table with triggers blocking UPDATE/DELETE.
- **UI**: optional React/Vite demo client; not a security boundary.

## Data model

All money is integer cents; currency is AUD only; timestamps are UTC ISO 8601.

| Table | Purpose | Key constraints |
|---|---|---|
| `payments` | Synthetic payments | `amount_cents > 0`, currency `AUD`, status `FAILED`/`SETTLED` |
| `accounts` | Synthetic accounts | status `ACTIVE`/`CLOSED`/`BLOCKED` |
| `cases` | Triage cases | `UNIQUE(payment_id)`, FK to payments |
| `proposals` | Planner proposals + policy outcome | `UNIQUE(case_id)` |
| `approvals` | Human decisions | `UNIQUE(proposal_id)` |
| `executions` | Mock execution results | `UNIQUE(proposal_id)` (prevents double execution) |
| `idempotency_keys` | Approval replay state | PK `key` |
| `audit_events` | Append-only audit trail | triggers reject UPDATE/DELETE |

## State machine

| From | Event | To |
|---|---|---|
| `OPEN` | triage → auto-approved | `EXECUTED` / `FAILED` |
| `OPEN` | triage → approval required | `PENDING_APPROVAL` |
| `OPEN` | triage → policy reject | `REJECTED` |
| `OPEN` | triage → planner abstain | `ESCALATED` |
| `PENDING_APPROVAL` | approve | `EXECUTED` / `FAILED` |
| `PENDING_APPROVAL` | reject | `REJECTED` |
| `PENDING_APPROVAL` | duplicate approve (same key) | same result, no re-execution |
| terminal | any write | `409 INVALID_STATE_TRANSITION` |

## API

| Method | Path | Role | Purpose |
|---|---|---|---|
| `POST` | `/v1/cases` | ANALYST | Create a case |
| `POST` | `/v1/cases/{id}/triage` | AGENT | Run planner + policy |
| `GET` | `/v1/cases/{id}` | ANALYST / APPROVER | Case view |
| `GET` | `/v1/cases?status=...` | ANALYST / APPROVER | List / approval queue |
| `POST` | `/v1/cases/{id}/approve` | APPROVER | Approve/reject; `Idempotency-Key` required |
| `GET` | `/v1/cases/{id}/audit` | ANALYST / APPROVER | Audit trail |
| `GET` | `/healthz`, `/readyz`, `/status` | public (local) | Liveness, readiness, counters |

The create-case request does not accept an assignee; the server sets it to the creating analyst. Business outcomes (policy rejection, escalation, executor failure) return 200 with the case status and reason codes. Transport/auth/validation errors use 4xx/5xx with a consistent error envelope.

## Policy, roles and separation of duties

- Tool allowlist and schema validation at the gateway.
- Action allowlist and action/exception compatibility.
- Refund amount > 0, ≤ original settled amount, ≤ the refund cap, and the account must be active.
- Retry only for failed, retryable payments on active accounts; amount fixed to the original.
- Refunds always require approval; retries above the auto-retry limit require approval.
- The approver must hold the `APPROVER` role and must not be the case assignee.
- Duplicate approval/execution replays the prior result.
- Degraded mode / `AUTO_APPROVE_ENABLED=false` forces approval for retries, refunds and holds.

## Security and privacy

- The agent has no execute tool; only read-only tools are exposed.
- The gateway keeps the repository private and exposes only the read tools; the deterministic planner is trusted code. An untrusted/LLM planner should run out of process.
- Policy is enforced in the service, not in a prompt.
- Synthetic API keys map to actor/role; production uses the enterprise IdP/OAuth.
- Parameterized SQL, Pydantic validation and a request-size limit.
- Requests must carry Content-Length; chunked bodies are rejected. The local guard is a backstop; production enforces the limit at the API gateway/WAF.
- PII redaction before logging or storing free text.
- No raw PII in logs or audit; audit stores hashes and reason codes.
- Append-only audit with UPDATE/DELETE triggers plus `recursive_triggers`, so INSERT OR REPLACE cannot bypass them.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- Tool calls are logged as structured `tool_called` events; the audit table records decisions.
- No outbound network calls in the core path.
- Production: SSO, Secrets Manager, KMS, least-privilege DB roles, WAF and retention policy.

## Failure handling and degrade

- Planner/tool failure → safe abstain, case `ESCALATED`.
- The planner runs under a real deadline; a hung planner returns an escalation. The worker is a daemon thread and cannot be force-killed.
- Executor failure → case `FAILED`; no automatic retry storm.
- Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. The mock is pure; production would use an outbox for exactly-once semantics.
- Policy documents are validated at startup (fail fast); a policy failure during triage escalates.
- Invalid config → fail fast at startup.
- DB unavailable → `/readyz` 503 (it probes the schema and does not create a new DB); writes fail closed.
- `DEGRADED_MODE` / `AUTO_APPROVE_ENABLED=false` → no automatic execution of retries, refunds or holds.
- Oversized request → 413; chunked request → 411; no side effect.
- Approval rationale is redacted and truncated to the field limit.
- Audit is append-only; UPDATE/DELETE/REPLACE are blocked at the database.

## Key trade-offs

- **Deterministic planner vs LLM**: reproducible, offline, no keys; a real LLM can implement the same protocol later without changing the controls.
- **SQLite vs managed DB**: zero local setup; production would use Aurora PostgreSQL.
- **Hand-written SQL vs ORM**: fewer dependencies and visible constraints; more boilerplate.
- **Synchronous execution vs queue**: simpler slice; production would use SQS/Step Functions.
- **API keys vs OAuth**: synthetic local stand-in; production uses the enterprise IdP.
- **Thin React UI vs no UI**: better walkthrough; the API/demo remain the core path.

## Production / cloud shape

API Gateway/ALB → ECS Fargate or Lambda → Aurora PostgreSQL; SQS/Step Functions for the workflow; S3 Object Lock for audit export; KMS/Secrets Manager; CloudWatch/OpenTelemetry; GitHub Actions → ECR → Terraform/CDK; dev → staging → prod with manual approvals, canary and rollback.

## CI/CD promotion

- **Local**: `python scripts/verify.py` (ruff + pytest + golden eval).
- **PR**: the same checks on GitHub Actions; optional UI build job.
- **Build**: container image, SBOM, image scan (future).
- **Dev**: Terraform deploy + smoke tests.
- **Staging**: integration tests against managed services, load test (future).
- **Prod**: manual approval, canary 5% → 25% → 100%, rollback on error/latency budget.

## Optional React UI

The `ui/` folder contains a small React/Vite client that calls the JSON API. It is a demo client only; it uses the synthetic demo keys, and the API remains the security boundary. It reuses one idempotency key per pending approval and only shows approval actions to approver roles. Production would use SSO and a backend-for-frontend, never service keys in the browser. The core path (API + demo + verify) does not depend on it.

## What was intentionally not built

- Real payment rails, real money, multi-currency/FX, partial refunds.
- A real LLM runtime or external model provider.
- Full IdP/OAuth, multi-level approval chains, delegated authority.
- Cloud deployment, Docker packaging, rate limiting, background workers, event sourcing.
