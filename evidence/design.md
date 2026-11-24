# Design

## Overview

The service is a small FastAPI application backed by SQLite. A deterministic planner gathers evidence through a read-only tool gateway and proposes an action. A policy engine validates the proposal. Low-risk actions may be auto-approved; financial or higher-risk actions require a human approver. A mock executor performs the action idempotently, and every state change is written to an append-only audit log.

The agent has no execute tool. The only write path is a proposal, and the service — not the planner — decides whether it can run.

## Architecture

```text
Client (browser UI / curl / demo script)
        |
        v
FastAPI API (auth, request ID, validation, size limit)
        |
        v
Case Workflow (state machine, idempotency, orchestration)
        |
        +--> Planner (deterministic; read-only tools only)
        |         |
        |         v
        |    Tool Gateway (allowlist + schema)
        |         +--> payment.lookup
        |         +--> account.lookup
        |         +--> policy.search
        |
        +--> Policy Engine (rules, roles, separation of duties)
                 |
                 +--> auto-approved --> Mock Executor (idempotent)
                 +--> pending approval --> Approver --> Mock Executor
                 +--> rejected
                 +--> escalated (safe abstain)
        |
        v
SQLite (cases, proposals, approvals, executions, audit)
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
- **UI**: optional thin static client; not a security boundary.

## State machine

TBD — see the implementation and `intent.md`.

## Key trade-offs

- **Deterministic planner vs LLM**: reproducible, offline, no keys; a real LLM can implement the same protocol later without changing the controls.
- **SQLite vs managed DB**: zero local setup; production would use Aurora PostgreSQL.
- **Hand-written SQL vs ORM**: fewer dependencies and visible constraints; more boilerplate.
- **Synchronous execution vs queue**: simpler slice; production would use SQS/Step Functions.
- **API keys vs OAuth**: synthetic local stand-in; production uses the enterprise IdP.

## Security and privacy

- The agent has no execute tool; tool allowlist and schema validation.
- The policy engine enforces amount caps, the original-amount cap, retryability, account status and action/exception compatibility.
- Separation of duties: the approver must be a different person from the case assignee.
- Idempotency keys and unique execution constraints prevent double execution.
- Append-only audit with UPDATE/DELETE triggers.
- No raw PII in logs or audit; free text is redacted.
- Synthetic data only; no outbound network calls in the core path.
- Production: SSO/OAuth, secrets manager, KMS, least-privilege DB roles, WAF and a retention policy.

## Production / cloud shape

API Gateway/ALB → ECS Fargate or Lambda → Aurora PostgreSQL; SQS/Step Functions for the workflow; S3 Object Lock for audit export; KMS/Secrets Manager; CloudWatch/OpenTelemetry; GitHub Actions → ECR → Terraform/CDK; dev → staging → prod with manual approvals, canary and rollback.

## CI/CD promotion

- **Local**: `python scripts/verify.py` (ruff + pytest + golden eval).
- **PR**: the same checks on GitHub Actions.
- **Build**: container image, SBOM, image scan (future).
- **Dev**: Terraform deploy + smoke tests.
- **Staging**: integration tests against managed services, load test (future).
- **Prod**: manual approval, canary 5% → 25% → 100%, rollback on error/latency budget.

## The optional UI

A static page at `/ui/` that calls the JSON API with synthetic demo keys. It exists to make the workflow and role denials visible; the API remains the security boundary. Production would use SSO and a backend-for-frontend, never service keys in the browser.
