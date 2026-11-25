# Intent

## Problem

Payment exceptions — failed, duplicate or disputed payments — are triaged manually. Operations staff gather evidence, apply policy and decide whether to retry, hold, escalate or request a refund. The work is repetitive, inconsistent and hard to audit.

An AI agent can gather evidence and propose an action, but it must not be trusted to execute financial operations. The service must enforce tool boundaries, policy limits, separation of duties and an audit trail independently of the planner.

## Intended users

- **Payment operations analyst**: owns the case and reviews the proposal.
- **Approver**: independently approves or rejects high-risk actions.
- **Risk / compliance auditor**: inspects the audit trail and policy decisions.
- **Platform engineer**: operates the service and reviews logs/status.

## Assumptions

- All data is synthetic; no real customers, accounts, payments or Westpac data.
- Single currency (AUD); amounts are integer cents.
- One approval level; no delegated authority or multi-step chains.
- The planner is deterministic and offline; no LLM runtime or external API key. The `Planner` protocol is the seam for an LLM (see `design.md`).
- The executor is a mock; no real money moves.
- SQLite is the local backing store; production would use a managed relational database.

## Constraints

- 3-hour build timebox, plus 15 minutes packaging.
- Runs locally from a clean checkout with no paid services, credentials or restricted data.
- Python 3.11+; FastAPI, Pydantic, SQLite; a small React/Vite UI served by the API (built assets committed).
- Public repository with incremental commits.
- One meaningful end-to-end workflow.

## Out of scope

- Real payment rails, real money movement, multi-currency/FX, partial refunds.
- Real LLM runtime or model provider integration; the expansion path is documented in `design.md`.
- Full identity provider/OAuth; multi-level approval chains.
- A production frontend/BFF and SSO; the demo UI is a thin client served by the API with synthetic keys.
- Cloud deployment; the production/CI-CD path is documented only.
- Rate limiting, background workers, event sourcing.

## Success criteria

- A synthetic case completes the full lifecycle: create → triage → policy → approval (when required) → mock execution → audit.
- Zero forbidden executions: no financial action executes without the required policy approval and, where required, a human approver.
- Duplicate approvals/executions are idempotent: one execution per proposal.
- Every state change has an audit event; audit rows cannot be updated or deleted.
- The golden-case eval passes with 100% expected outcomes and zero forbidden executions.
- `python scripts/verify.py` passes from a clean checkout.
- `python scripts/demo.py` runs the workflow offline in under 60 seconds with no configuration.
- The core path makes no outbound network calls.
- The UI is the primary demo surface; it explains the roles, rules and sample cases without the README, and the built assets run without Node.
- One identity can hold multiple roles; the active role is explicit and recorded. The approver cannot be the case assignee or the triage requester; Alice and Bob can approve each other's cases, Carol cannot approve and Dave cannot triage.
- The audit response is bounded and the scalability limits and production path are documented (`design.md`, ADR-8, `operate.md`).
- The CAP choices and distributed failure behavior are documented: CP for case/approval/execution, AP for reads/observability (`design.md`, ADR-9).
