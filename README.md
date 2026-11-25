# Payment Exception Triage

A locally runnable demo of a bank payment-exception workflow where an AI agent can gather evidence and propose an action, but **cannot execute**. The service enforces policy, human approval, separation of duties, idempotency and an append-only audit.

**The React UI is the primary way to explore the app.** Start the API, open `http://127.0.0.1:8000/ui/`, and follow the on-screen guide. The built UI is committed, so no Node install is required to run it.

All data is synthetic; no real customers, accounts, payments or Westpac data.

## The 30-second version

A payment fails, duplicates or is disputed. An operations analyst opens a case. The agent reads payment, account and policy evidence and proposes Retry, Hold, Escalate or Refund. Low-risk retries auto-execute; refunds and higher-risk retries wait for a different human approver. Every step is audited. The agent has no execute tool.

## Roles

| Role | In the demo | Can do | Cannot do |
|---|---|---|---|
| **Agent service** | `agent-key` (used by the Triage button) | Read payment/account/policy evidence; propose an action | Execute, write or approve |
| **Analyst** | Analyst Alice (`analyst-key`) | View cases/audit trails; run the agent triage | Approve |
| **Approver** | Approver Bob (`approver-key`) | Approve or reject pending actions | Triage |
| **Separation-of-duties test** | Approver Alice (`approver-alice-key`) | Attempt approval; denied because she owns the seeded cases | Approve her own case |
| **Auditor / compliance** | Uses analyst/approver access in this demo | Inspect the append-only audit trail | Change audit records |

The demo keys are synthetic and local-only. Production would use the enterprise IdP/OAuth.

## The rules the service enforces

| Rule | What it means |
|---|---|
| **Read-only tools** | The agent can call only `payment.lookup`, `account.lookup` and `policy.search`. |
| **No execute tool** | The agent cannot retry, refund, hold or change a case; it can only propose. |
| **Auto-retry limit** | Retries up to **$1,000** execute automatically; above that a human approver is required. |
| **Refund cap** | Refunds are capped at **$10,000** and at the original settled amount. |
| **Refund eligibility** | The payment must be settled and the account active. |
| **Hold / escalate** | Non-financial. Hold auto-executes; escalate routes to a human and ends the automated flow. |
| **Separation of duties** | The approver must not be the case assignee/creator. |
| **One proposal / approval / execution** | Unique constraints; duplicate approvals replay the stored result instead of executing twice. |
| **Audit** | Append-only. UPDATE/DELETE/REPLACE are blocked at the database. |
| **Degraded mode** | `DEGRADED_MODE=true` or `AUTO_APPROVE_ENABLED=false` forces approval for retries, refunds and holds. |
| **Failure handling** | Planner/tool failure or timeout escalates; executor failure marks the case failed; no automatic retry storm. |

The UI reads the live auto-retry limit, refund cap and mode from `/status` — the caps in the **Rules** panel, the mode in the status bar.

## Sample cases

The seed creates eight open cases. The UI's **Sample cases** panel shows the same guide. Match the IDs to see the expected outcomes.

| Case | Scenario | Amount | Expected outcome |
|---|---|---|---|
| `CASE-1001` | Temporary error, active account | $250 | Auto-retry → **EXECUTED** |
| `CASE-1002` | Temporary error, above the auto-retry limit | $5,000 | `RETRY` pending → approve → **EXECUTED** |
| `CASE-1003` | Insufficient funds | $1,200 | `HOLD` auto → **EXECUTED** |
| `CASE-1004` | Account closed | $900 | **ESCALATED** |
| `CASE-1005` | Limit exceeded | $3,000 | **ESCALATED** |
| `CASE-1006` | Duplicate of `PAY-1000` | $1,500 | `REFUND_REQUEST` pending → approve → **EXECUTED** |
| `CASE-1007` | Customer dispute | $2,500 | `REFUND_REQUEST` pending → approve → **EXECUTED** |
| `CASE-1008` | Customer dispute over the refund cap | $20,000 | **ESCALATED** |

## Quick start (UI)

Prerequisites: **Python 3.11+**. Node is not required to run the committed UI.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
python -m pip install -e ".[dev]"

python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

Open **http://127.0.0.1:8000/ui/**.

The built UI is committed in `ui/dist`. Node 22+ is only needed if you change the UI:

```bash
cd ui
npm ci
npm run build
```

No `.env` is required. `env.example` documents optional overrides.

### Two-minute walkthrough

1. As **Analyst Alice**, click **Triage** on `CASE-1001`. The agent proposes a retry; it auto-executes. Click **Details** to see the rationale and evidence, then **Audit** for the trail.
2. Triage `CASE-1002`. It waits for approval. Switch to **Approver Alice** and try to approve it: the service denies it with `SELF_APPROVAL_DENIED` — she owns the seeded case (separation of duties). Switch to **Approver Bob** and approve it; it executes.
3. Switch back to **Analyst Alice** and triage `CASE-1006` (duplicate) and `CASE-1007` (dispute). Switch to **Approver Bob** and approve both; the refunds execute.
4. Switch back to **Analyst Alice** and triage `CASE-1008` (over cap) and `CASE-1004` (closed account). Both escalate; nothing executes.
5. The UI reuses one idempotency key per approval dialog, so a double-click or retry cannot double-execute. The server tests and `scripts/demo.py` also demonstrate the replay.

## What the UI shows

- **How to use** — three steps and the role/separation-of-duties hint.
- **Roles** — who can view, triage (agent), approve or audit.
- **Rules** — the live auto-retry limit and refund cap from `/status`, plus the fixed controls.
- **Sample cases** — the table above.
- **Case table** — status, proposal action, policy outcome and reason codes.
- **Details** — the proposal rationale, evidence references, approval and execution.
- **Audit** — the append-only event trail.
- **Status bar** — mode, case counts, auto-retry limit and refund cap.

The UI is a demo client; the API enforces every role, policy and state check. It uses synthetic demo keys and is not a security boundary.

## For engineers

### Architecture

```text
Browser UI / curl / demo
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

### API

| Method | Path | Role | Purpose |
|---|---|---|---|
| `POST` | `/v1/cases` | ANALYST | Create a case |
| `POST` | `/v1/cases/{id}/triage` | AGENT | Run planner + policy |
| `GET` | `/v1/cases/{id}` | ANALYST / APPROVER | Case view |
| `GET` | `/v1/cases?status=...` | ANALYST / APPROVER | List / approval queue |
| `POST` | `/v1/cases/{id}/approve` | APPROVER | Approve/reject; `Idempotency-Key` required |
| `GET` | `/v1/cases/{id}/audit` | ANALYST / APPROVER | Audit trail |
| `GET` | `/healthz`, `/readyz`, `/status` | public (local) | Liveness, readiness, counters, policy caps |

OpenAPI docs: `http://127.0.0.1:8000/docs`.

### API example

```bash
curl -s http://127.0.0.1:8000/v1/cases -H "X-API-Key: analyst-key"

curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/triage \
  -H "X-API-Key: agent-key"

curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/approve \
  -H "X-API-Key: approver-key" \
  -H "Idempotency-Key: demo-approval-1" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVE","rationale":"Reviewed the evidence"}'

curl -s http://127.0.0.1:8000/v1/cases/CASE-1002/audit \
  -H "X-API-Key: analyst-key"
```

### State machine

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

### Verification

```bash
python scripts/verify.py
```

Runs `ruff`, `pytest` and the 30-case golden eval. The same command runs in GitHub Actions, together with a UI build job. The golden eval is a regression/acceptance gate, not a model-quality benchmark.

```bash
python scripts/demo.py
```

Runs the lifecycle without the UI.

### Known limitations

- The planner is deterministic, not an LLM. The exercise allows non-AI products; the AI-agent requirement is the development process. A real LLM can implement the same `Planner` protocol without changing the controls. The expansion path is in `evidence/design.md` and ADR-7.
- The executor is a mock; no real payment rails or money movement. Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. Production would use an outbox for exactly-once semantics.
- SQLite is embedded for zero-setup local run; production would use a managed relational database.
- API keys are synthetic; production would use the enterprise IdP/OAuth.
- One approval level; no multi-level approval chains.
- The slice's separation-of-duties check is the case assignee; production would also exclude the human triage requester and proposal editors (see "Production identity and AI expansion").
- AUD only; no FX or partial refunds.
- No Docker or cloud deployment; the production/CI-CD path is documented in `evidence/design.md`.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- The deterministic planner is trusted code. An untrusted or LLM planner should run out of process; the gateway keeps the repository private, but Python attribute access is not a sandbox.
- Chunked request bodies are rejected with 411; the request-size guard uses Content-Length. Production would enforce the limit at the API gateway/WAF.
- The UI has a TypeScript/Vite build check, a server-side smoke test and an asset-serving check, but no browser automation.
- No rate limiting; production would add it at the gateway.

### Production identity and AI expansion

The demo gives each key one role and uses two keys for the same actor (Alice) to demonstrate multi-role and separation of duties. Production would source a role set from the IdP, require an explicit active role, use a separate service identity for the agent/scheduler, and exclude every human who created, owns, requested triage for or edited the case from approving it — not just the assignee. The slice's check is `approver != assignee`; the production model is in `evidence/design.md` and ADR-6.

The deterministic planner is a stand-in. An LLM can implement the same `Planner` protocol behind the read-only tool gateway, with structured output, evidence grounding, confidence/abstain, deterministic fallback and the same service-side policy, approval, separation of duties and audit. The phased path, guardrails and what stays deterministic are in `evidence/design.md` and ADR-7. The LLM would propose; it would never execute or approve.

### Evidence pack

- [`evidence/intent.md`](evidence/intent.md) — problem, users, assumptions, success criteria
- [`evidence/design.md`](evidence/design.md) — architecture, trade-offs, security, cloud shape
- [`evidence/agentic-workflow.md`](evidence/agentic-workflow.md) — how Claude Code was used and reviewed
- [`evidence/verify.md`](evidence/verify.md) — test/eval results, code-review dispositions, known gaps
- [`evidence/operate.md`](evidence/operate.md) — runbook, logs/status, failure modes, degrade/rollback
- [`evidence/decisions.md`](evidence/decisions.md) — ADRs and what was intentionally not built

### Repository layout

```text
src/payment_triage/   application code
scripts/              seed, demo, eval, verify
sample-data/          synthetic accounts, payments, policies, golden cases
tests/                unit, integration, security, idempotency, eval gate
ui/                   React/Vite client (source + committed dist)
evidence/             engineering evidence pack
```
