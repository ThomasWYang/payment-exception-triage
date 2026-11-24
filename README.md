# Payment Exception Triage

A locally runnable payment exception triage service with a guarded agent workflow.

The planner gathers evidence and proposes an action; it cannot execute. Tool allowlisting, policy checks, human approval, idempotency and audit are enforced by the service — not by a prompt.

> Status: under active development. Full setup, demo and verification instructions will be added before submission.

## Evidence pack

See `evidence/`:

- `intent.md`
- `design.md`
- `agentic-workflow.md`
- `verify.md`
- `operate.md`
- `decisions.md`
