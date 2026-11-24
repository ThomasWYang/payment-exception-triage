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
- The planner is deterministic and offline; no LLM runtime or external API key.
- The executor is a mock; no real money moves.
- SQLite is the local backing store; production would use a managed relational database.

## Constraints

- 3-hour build timebox, plus 15 minutes packaging.
- Runs locally from a clean checkout with no paid services, credentials or restricted data.
- Python 3.11+; FastAPI, Pydantic, SQLite; no frontend framework.
- Public repository with incremental commits.
- One meaningful end-to-end workflow.

## Out of scope

- Real payment rails, real money movement, multi-currency/FX, partial refunds.
- Real LLM runtime or model provider integration.
- Full identity provider/OAuth; multi-level approval chains.
- Browser SPA; the optional `/ui` page is a thin demo client.
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
