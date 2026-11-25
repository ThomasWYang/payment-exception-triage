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
- **UI**: primary React/Vite demo client, served at `/ui/` from committed assets; not a security boundary.

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
| `GET` | `/healthz`, `/readyz`, `/status` | public (local) | Liveness, readiness, counters and policy caps |

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

## Production identity, multi-role and four-eye

The slice gives each API key one role and uses two keys for the same actor (`user:alice`: `analyst-key` and `approver-alice-key`) to demonstrate multi-role and separation of duties. Production would model roles differently:

- **Roles are a set from the IdP**, not one credential per role. The token carries `sub` and `roles`; the service checks membership and requires an explicit active role for the action (or derives it from the endpoint and records it). The active role is shown in the UI and written to the audit. Do not silently pick the highest privilege. Example: `roles={"analyst","approver"}`, `active_role="approver"`.
- **Service identities are separate.** The agent/scheduler has a dedicated `AGENT`/`SERVICE` identity with read-and-propose only. It never holds an analyst or approver role. If it creates cases, it does so as a service actor and the case is assigned to a human or queue.
- **The case records participants**: `created_by`, `assigned_to`, `triage_requested_by`, `proposed_by`, `approved_by`. The agent may be `proposed_by`; the human approver must be independent.
- **Four-eye / maker-checker is enforced on actor identity and case participation**, not on role alone. The approver must be a human with the approver role and must not be the creator, assignee, triage requester, proposal author/editor or beneficiary. A user may hold both analyst and approver roles; they can act in either, but the same actor cannot be both maker and checker for the same case.
- **No independent approver**: leave the case pending or escalate; do not allow self-approval. A break-glass path needs a second approver or supervisor and a specific audit event.
- **Operations**: periodic access reviews, least privilege, separate human/service accounts, monitoring for toxic combinations, and a queue for small teams.

The slice's check is `approver.actor != case.assignee` (`policy.py`). It is sufficient for the seeded single-analyst demo: every case is assigned to `user:alice`, so her approver credential is denied and Bob can approve. It does not record a human triage requester when the UI uses the agent key, and it does not check proposal editors. Production would extend the check to the participant history above; this is a deliberate slice simplification.

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

- **Deterministic planner vs LLM**: reproducible, offline, no keys; a real LLM can implement the same protocol later without changing the controls (see the LLM/AI expansion section).
- **SQLite vs managed DB**: zero local setup; production would use Aurora PostgreSQL.
- **Hand-written SQL vs ORM**: fewer dependencies and visible constraints; more boilerplate.
- **Synchronous execution vs queue**: simpler slice; production would use SQS/Step Functions.
- **API keys vs OAuth**: synthetic local stand-in; production uses the enterprise IdP.
- **Thin React UI vs no UI**: the UI is the primary demo surface; it makes the roles, rules and outcomes visible without reading the docs, while the API remains the core and the security boundary.

## LLM/AI expansion (design-only)

The slice deliberately uses a deterministic planner: reproducible tests, offline demo, no keys or model provider. The seam for a real model is the `Planner` protocol (`planner.py`): `plan(case, gateway) -> Proposal`. An `LLMPlanner` can implement it without changing the tool gateway, policy, approval, executor or audit. The model would run out of process and get only the read-only tools.

**Where an LLM adds value:** evidence summarization with citations, exception classification from free-text notes, policy retrieval/RAG, rationale drafting, case prioritization, customer-communication drafts, anomaly/fraud signals, and multi-step read-only investigation.

**What stays deterministic:** the policy limits, approval requirements, separation of duties, idempotency, execution and audit. The LLM proposes; the service decides and executes.

**Guardrails:**
- Structured output (JSON schema/Pydantic); invalid output → escalate.
- Read-only tool allowlist, schema validation, max tool calls, wall-clock/token/cost budgets.
- Prompt-injection defenses: treat payment/customer text as untrusted data; delimit it; never follow instructions from it; validate the output and actions.
- PII redaction before the model call; private endpoint/contract; no training on bank data; retention/residency/DPIA.
- Evidence grounding: require citations to tool-result refs; the service verifies the refs; unsupported claims → escalate.
- Confidence/abstain; low confidence → escalate; never auto-approve on confidence.
- Deterministic fallback and a kill switch (degraded mode) on timeout/error/invalid output.
- Audit the model ID, prompt version/hash, tool calls and proposal; pin versions and change-control prompts.
- Evals: golden cases plus adversarial/prompt-injection, grounding/hallucination, policy compliance, cost/latency; shadow mode and canary before exposure.

**Phased rollout:**
1. Deterministic planner (current baseline).
2. Shadow LLM: run in parallel, compare to deterministic/golden, no execution.
3. LLM-assisted proposal with human approval and deterministic fallback, starting with low-risk cases.
4. Multi-step read-only agent with budgets and the same approval/audit.
5. Optimize with RAG/fine-tuning, expand exception types, and tune cost/latency.

**Do not:** give the LLM execute tools; put policy only in the prompt; trust the rationale or evidence without verification; auto-approve on model confidence; send PII/secrets without redaction/contract; use the LLM as the audit source; let it choose or bypass the approver.

## Production / cloud shape

API Gateway/ALB → ECS Fargate or Lambda → Aurora PostgreSQL; SQS/Step Functions for the workflow; S3 Object Lock for audit export; KMS/Secrets Manager; CloudWatch/OpenTelemetry; GitHub Actions → ECR → Terraform/CDK; dev → staging → prod with manual approvals, canary and rollback.

## CI/CD promotion

- **Local**: `python scripts/verify.py` (ruff + pytest + golden eval).
- **PR**: the same checks on GitHub Actions plus the UI build job.
- **Build**: container image, SBOM, image scan (future).
- **Dev**: Terraform deploy + smoke tests.
- **Staging**: integration tests against managed services, load test (future).
- **Prod**: manual approval, canary 5% → 25% → 100%, rollback on error/latency budget.

## React UI (primary demo surface)

The `ui/` folder contains a small React/Vite client that calls the JSON API and is the primary way to explore the app. The built assets are committed in `ui/dist` and served by FastAPI at `/ui/`, so running the demo needs only Python; Node 22+ is required only to rebuild. The screen explains the three-step flow, roles, rules, sample cases and expected outcomes, and shows case details, evidence, approval and audit. It reads the live auto-retry limit, refund cap and mode from `/status`. Triage is offered to the analyst role; approval actions are offered to approver roles. It reuses one idempotency key per pending approval. It is a demo client only; it uses the synthetic demo keys, and the API remains the security boundary. Production would use SSO and a backend-for-frontend, never service keys in the browser. The core API and demo do not depend on the UI, but the UI is the recommended walkthrough.

## What was intentionally not built

- Real payment rails, real money, multi-currency/FX, partial refunds.
- A real LLM runtime or external model provider; the expansion path is documented above.
- Full IdP/OAuth, multi-level approval chains, delegated authority; the production multi-role/four-eye model is documented above.
- Cloud deployment, Docker packaging, rate limiting, background workers, event sourcing.
