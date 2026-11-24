# Payment Exception Triage

A locally runnable payment exception triage service with a guarded agent workflow.

The planner gathers evidence and proposes an action; it cannot execute. Tool allowlisting, policy checks, human approval, idempotency and audit are enforced by the service — not by a prompt.

This is a technical exercise submission. All data is synthetic; it contains no real customers, accounts, payments or Westpac data.

## What it does

- Triages three exception types: failed payments, duplicate payments and customer disputes.
- Gives the planner three read-only tools: `payment.lookup`, `account.lookup`, `policy.search`.
- Enforces policy in the service: action allowlist, amount caps, original-amount cap, retryability, account status, roles and separation of duties.
- Tiered approval: low-risk retries auto-approve; refunds and higher-risk retries require a different human approver.
- Executes through a mock payment executor; no real money moves.
- Writes an append-only audit trail; audit rows cannot be updated or deleted.
- Makes approvals and executions idempotent; duplicate requests do not double-execute.
- Fails closed: planner/tool failures escalate, executor failures mark the case failed, and degraded mode disables auto-approval.

## Prerequisites

- Python 3.11+ (tested on Python 3.13)
- No API keys, paid services, cloud accounts or external databases
- Optional: Node.js 22+ for the React UI

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

No `.env` is required. `env.example` documents optional overrides (database path, caps, API keys, CORS origins). The defaults are synthetic and local-only.

## Run the demo

```bash
python scripts/demo.py
```

It runs the full lifecycle in a temporary database: auto-approved retry, high-value retry with approval, duplicate refund with approval, dispute over cap, closed account, self-approval denial and idempotent duplicate approval.

Sample output:

```text
Scenario                 Outcome        Detail
----------------------------------------------------------------------
auto retry               EXECUTED       execution=SUCCEEDED
high-value retry         EXECUTED       approved by user:bob
duplicate refund         EXECUTED       approved by user:bob
dispute over cap         ESCALATED      AMOUNT_EXCEEDS_LIMIT
closed account           ESCALATED      ACCOUNT_NOT_ACTIVE
self-approval            DENIED         SELF_APPROVAL_DENIED
duplicate approval       IDEMPOTENT     executions=1
```

## Run the API

```bash
python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the OpenAPI UI.

The seed creates 8 open cases from synthetic sample data. The synthetic API keys are:

| Role | Key | Actor |
|---|---|---|
| Agent | `agent-key` | `agent:triage-bot` |
| Analyst | `analyst-key` | `user:alice` |
| Approver | `approver-key` | `user:bob` |
| Approver (separation-of-duties test) | `approver-alice-key` | `user:alice` |

These are demo keys; production uses the enterprise IdP/OAuth. Do not reuse them.

### Example workflow (curl)

List the seeded cases:

```bash
curl -s http://127.0.0.1:8000/v1/cases -H "X-API-Key: analyst-key"
```

Triage `CASE-1002` (a $5,000 retry, so it requires approval):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/triage -H "X-API-Key: agent-key"
```

Approve it:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/approve \
  -H "X-API-Key: approver-key" \
  -H "Idempotency-Key: demo-approval-1" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVE","rationale":"Reviewed the evidence"}'
```

Inspect the audit trail:

```bash
curl -s http://127.0.0.1:8000/v1/cases/CASE-1002/audit -H "X-API-Key: analyst-key"
```

Create a new case (`PAY-1000` is a settled payment, so a customer dispute is valid):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/cases \
  -H "X-API-Key: analyst-key" \
  -H "Content-Type: application/json" \
  -d '{"payment_id":"PAY-1000","exception_type":"CUSTOMER_DISPUTE"}'
```

## Verify

```bash
python scripts/verify.py
```

This runs:

- `ruff check .`
- `pytest`
- `scripts/eval.py` — 30 golden cases with exact expected outcomes, zero forbidden executions and an idempotency replay check.

The same command runs in GitHub Actions. The golden eval is a regression/acceptance gate, not a model-quality benchmark.

## How it works

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
        |         +--> payment.lookup / account.lookup / policy.search
        |
        +--> Policy Engine (limits, roles, separation of duties)
                 +--> auto-approved --> Mock Executor
                 +--> pending approval --> human approver --> Mock Executor
                 +--> rejected / escalated
        |
        v
SQLite + append-only audit + JSON logs + /healthz /readyz /status
```

The planner has no execute tool. The only write path is a proposal, and the service decides whether it can run.

### Safety properties

- **Validation rejection**: invalid payloads, unknown roles, oversized requests and unknown tools are rejected with no side effects.
- **Guarded failure**: planner/tool errors escalate; executor errors mark the case failed; a database outage fails closed.
- **Degrade mode**: `DEGRADED_MODE=true` or `AUTO_APPROVE_ENABLED=false` forces human approval for retries, refunds and holds.
- **Safe abstain**: missing or conflicting evidence escalates instead of guessing.
- **Separation of duties**: the server sets the case assignee to the creating analyst; the approver must be a different person.
- **Refund controls**: refunds require a settled payment and an active account, and are capped at the original amount and the configured cap.
- **Idempotency**: one execution per proposal; duplicate approvals replay the original result.
- **Audit integrity**: SQLite triggers reject UPDATE/DELETE, and recursive triggers block INSERT OR REPLACE.
- **Request size**: requests must carry `Content-Length`; chunked requests are rejected with 411.
- **PII**: no raw PII in logs or audit; free text is redacted.

## Optional React UI

The `ui/` folder contains a small React/Vite client that calls the API. The core path does not depend on it; `/docs` and the demo script are sufficient.

```bash
cd ui
npm install
npm run build
```

Start the API and open `http://127.0.0.1:8000/ui/`. For UI development, run `npm run dev` and open `http://localhost:5173/ui/`. See `ui/README.md` for details.

## Known limitations

- The planner is deterministic, not an LLM. The exercise allows non-AI products; the AI-agent requirement is the development process. A real LLM can implement the same `Planner` protocol without changing the controls.
- The executor is a mock; no real payment rails or money movement.
- SQLite is embedded for zero-setup local run; production would use a managed relational database.
- API keys are synthetic; production would use the enterprise IdP/OAuth.
- One approval level; no multi-level approval chains.
- AUD only; no FX or partial refunds.
- No Docker or cloud deployment; the production/CI-CD path is documented in `evidence/design.md`.
- The golden eval is a synthetic regression gate, not a model-quality benchmark.
- Execution is at-least-once at the workflow boundary. The executor receives the proposal ID as a durable idempotency key and must dedupe; the mock is pure. Production would use an outbox for exactly-once semantics.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- The deterministic planner is trusted code. An untrusted or LLM planner should run out of process; the gateway keeps the repository private, but Python attribute access is not a sandbox.
- Chunked request bodies are rejected with 411; the request-size guard uses Content-Length. Production would enforce the limit at the API gateway/WAF.
- The UI has a TypeScript/Vite build check and a server-side smoke test, but no browser automation.
- No rate limiting; production would add it at the gateway.
- `/status` is public locally; restrict it in production.

## Evidence pack

- [`evidence/intent.md`](evidence/intent.md) — problem, users, assumptions, success criteria
- [`evidence/design.md`](evidence/design.md) — architecture, trade-offs, security, cloud shape
- [`evidence/agentic-workflow.md`](evidence/agentic-workflow.md) — how Claude Code was used and reviewed
- [`evidence/verify.md`](evidence/verify.md) — test/eval results, known gaps, local vs CI
- [`evidence/operate.md`](evidence/operate.md) — runbook, logs/status, failure modes, degrade/rollback
- [`evidence/decisions.md`](evidence/decisions.md) — ADRs and what was intentionally not built

## Repository layout

```text
src/payment_triage/   application code
scripts/              seed, demo, eval, verify
sample-data/          synthetic accounts, payments, policies, golden cases
tests/                unit, integration, security, idempotency, eval gate
evidence/             engineering evidence pack
ui/                   optional React/Vite client
```
