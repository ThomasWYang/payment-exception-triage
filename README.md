# Payment Exception Triage

A locally runnable demo of a bank payment-exception workflow where an AI agent can gather evidence and propose an action, but **cannot execute**. The service enforces policy, human approval, separation of duties, idempotency and an append-only audit.

**The React UI is the primary way to explore the app.** Start the API, open `http://127.0.0.1:8000/ui/`, and follow the on-screen guide. The built UI is committed, so no Node install is required to run it.

All data is synthetic; no real customers, accounts, payments or Westpac data.

## The 30-second version

A payment fails, duplicates or is disputed. An operations analyst opens a case. The agent reads payment, account and policy evidence and proposes Retry, Hold, Escalate or Refund. Low-risk retries auto-execute; refunds and higher-risk retries wait for a different human approver. Every step is audited. The agent has no execute tool.

## Roles

| Role | In the demo | Can do | Cannot do |
|---|---|---|---|
| **Agent service** | `agent-key` (AGENT) | Read payment/account/policy evidence; propose; service- or scheduler-initiated triage | Execute, approve or write |
| **Alice** | `alice-key` (ANALYST + APPROVER) | View; request triage; approve other people's cases | Approve a case she owns (assignee) or requested triage for |
| **Bob** | `bob-key` (ANALYST + APPROVER) | View; request triage; approve other people's cases | Approve a case he owns (assignee) or requested triage for |
| **Carol** | `carol-key` (ANALYST) | View; request triage | Approve |
| **Dave** | `dave-key` (APPROVER) | View; approve or reject pending actions | Triage |

Alice and Bob hold both roles so they can approve each other's cases; the service still blocks self-approval and the triage requester. Carol and Dave show the single-role boundaries. Each identity has one key; the UI selects the active role and the API records it. The demo keys are synthetic and local-only; production would use the enterprise IdP/OAuth.

## The rules the service enforces

| Rule | What it means |
|---|---|
| **Read-only tools** | The agent can call only `payment.lookup`, `account.lookup` and `policy.search`. |
| **No execute tool** | The agent cannot retry, refund, hold or change a case; it can only propose. |
| **Auto-retry limit** | Retries up to **$1,000** execute automatically; above that a human approver is required. |
| **Refund cap** | Refunds are capped at **$10,000** and at the original settled amount. |
| **Refund eligibility** | The payment must be settled and the account active. |
| **Hold / escalate** | Non-financial. Hold auto-executes; escalate routes to a human and ends the automated flow. |
| **Separation of duties** | The approver must not be the case assignee/creator or the analyst who requested triage. |
| **Multiple roles** | One identity can hold analyst and approver roles; the request selects the active role, and the service records it. SoD is on the actor and case participation, not the role alone. |
| **One proposal / approval / execution** | Unique constraints; duplicate approvals replay the stored result instead of executing twice. |
| **Audit** | Append-only. UPDATE/DELETE/REPLACE are blocked at the database. |
| **Degraded mode** | `DEGRADED_MODE=true` or `AUTO_APPROVE_ENABLED=false` forces approval for retries, refunds and holds. |
| **Failure handling** | Planner/tool failure or timeout escalates; executor failure marks the case failed; no automatic retry storm. |

The UI reads the live auto-retry limit, refund cap and mode from `/status` — the caps in the **Rules** panel, the mode in the status bar.

## Sample cases

The seed creates ten open cases, assigned across all four humans. The UI's **Sample cases** panel shows the same guide, including the owner. Match the IDs to see the expected outcomes.

| Case | Owner | Scenario | Amount | Expected outcome |
|---|---|---|---|---|
| `CASE-1001` | Carol | Temporary error, active account | $250 | Auto-retry → **EXECUTED** |
| `CASE-1002` | Alice | Temporary error, above the auto-retry limit | $5,000 | `RETRY` pending → approve → **EXECUTED** |
| `CASE-1003` | Dave | Insufficient funds | $1,200 | `HOLD` auto → **EXECUTED** |
| `CASE-1004` | Bob | Account closed | $900 | **ESCALATED** |
| `CASE-1005` | Alice | Limit exceeded | $3,000 | **ESCALATED** |
| `CASE-1006` | Dave | Duplicate of `PAY-1000` | $1,500 | `REFUND_REQUEST` pending → approve → **EXECUTED** |
| `CASE-1007` | Dave | Customer dispute | $2,500 | `REFUND_REQUEST` pending → approve → **EXECUTED** |
| `CASE-1008` | Alice | Customer dispute over the refund cap | $20,000 | **ESCALATED** |
| `CASE-1009` | Carol | Temporary error, above the auto-retry limit | $5,000 | `RETRY` pending → approve → **EXECUTED** |
| `CASE-1010` | Bob | Temporary error, above the auto-retry limit | $5,000 | `RETRY` pending → approve → **EXECUTED** |

### Case ownership, triage requester and approver

Each case has three distinct human concepts:

- **Assignee** — the human who owns and handles the case. For cases created through the API, it is the analyst who created the case; the seed assigns the demo owners directly. The server sets it and clients cannot choose it. The assignee is not an approval role.
- **Triage requester** — the human analyst or agent service that requested triage; recorded in `triage_requested_by`. Any analyst (or the agent service) can request triage on any OPEN case, not just the assignee. The assignee remains the owner.
- **Approver** — a different human acting with the APPROVER role. The approver must not be the assignee or the triage requester: the service denies `SELF_APPROVAL_DENIED` or `TRIAGE_REQUESTER_CANNOT_APPROVE`.

The seed spreads the ten cases across all four humans so the combinations are visible:

| Owner | Cases | Demonstrates |
|---|---|---|
| Alice (analyst + approver) | 1002, 1005, 1008 | Owns cases; can triage and approve other people's cases; cannot approve her own or a case she requested triage for |
| Bob (analyst + approver) | 1004, 1010 | Same cross-approval boundary |
| Carol (analyst only) | 1001, 1009 | Can request triage; cannot approve |
| Dave (approver only) | 1003, 1006, 1007 | Cannot triage; can approve other people's cases; cannot approve his own |

Examples: Carol requests triage on `CASE-1009` and Dave approves it; Bob requests triage on `CASE-1010` and Alice approves it; Alice requests triage on `CASE-1002` and is denied, so Bob approves; Dave owns `CASE-1006`/`CASE-1007` but cannot triage, so Carol requests and Bob approves; Dave owns `CASE-1003`, another analyst requests triage, and the hold auto-executes.

Why any analyst can triage: triage only gathers evidence and produces a proposal; it cannot execute or approve. Recording who requested it is what makes the four-eye check meaningful. A stricter production policy could restrict triage to the assignee, team or queue.

## Quick start (UI)

Prerequisites: **Python 3.11+**. Node is not required to run the committed UI.

```bash
python -m venv .venv
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# Windows (cmd.exe)
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

1. As **Carol (analyst)**, triage `CASE-1001` (auto-executes) and `CASE-1009` (Carol's case; waits for approval). Click **Details** to see the rationale, evidence and `Triage requested by user:carol`.
2. Switch to **Dave (approver)** and approve `CASE-1009`; it executes. Carol has no approval actions; Dave has no Triage button.
3. Switch to **Bob (analyst)** and triage `CASE-1010` (Bob's case); switch to **Alice (approver)** and approve it. Alice and Bob can approve each other's cases.
4. Switch to **Alice (analyst)** and triage `CASE-1002` (Alice's case); switch to **Alice (approver)** and try to approve: denied with `SELF_APPROVAL_DENIED` and `TRIAGE_REQUESTER_CANNOT_APPROVE`. Switch to **Bob (approver)** and approve it.
5. Switch to **Carol (analyst)** and triage `CASE-1006` (Dave's duplicate) and `CASE-1007` (Dave's dispute); switch to **Bob (approver)** and approve both. Dave owns them but cannot triage (approver-only) or approve them (assignee). Switch back to **Carol (analyst)** and triage `CASE-1008` (Alice's over-cap) and `CASE-1004` (Bob's closed account); both escalate.
6. The UI reuses one idempotency key per approval dialog, so a double-click or retry cannot double-execute. The server tests and `scripts/demo.py` also demonstrate the replay.

## What the UI shows

- **How to use** — the user/active-role walkthrough and the separation-of-duties hint.
- **Roles** — Alice/Bob (analyst + approver), Carol (analyst), Dave (approver) and the agent service.
- **Rules** — the live auto-retry limit and refund cap from `/status`, plus the fixed controls.
- **Sample cases** — the table above.
- **Case table** — status, proposal action, policy outcome and reason codes.
- **Details** — the proposal rationale, evidence references, assignee, triage requester, approval and execution.
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
| `POST` | `/v1/cases/{id}/triage` | ANALYST / AGENT | Request triage; run planner + policy |
| `GET` | `/v1/cases/{id}` | ANALYST / APPROVER | Case view |
| `GET` | `/v1/cases?status=...` | ANALYST / APPROVER | List / approval queue |
| `POST` | `/v1/cases/{id}/approve` | APPROVER | Approve/reject; `Idempotency-Key` required |
| `GET` | `/v1/cases/{id}/audit` | ANALYST / APPROVER | Audit trail; paginated (`limit` default 500, max 1000; `offset`) |
| `GET` | `/healthz`, `/readyz`, `/status` | public (local) | Liveness, readiness, counters, policy caps |

One identity can hold multiple roles. Send `X-Acting-Role: ANALYST`, `APPROVER` or `AGENT` (the agent service uses `AGENT`) to select the active role; the service records it. If omitted, the service selects the first role the identity holds that the endpoint allows. OpenAPI docs: `http://127.0.0.1:8000/docs`.

### API example

```bash
# Alice holds both roles; act as analyst for the case list.
curl -s http://127.0.0.1:8000/v1/cases \
  -H "X-API-Key: alice-key" -H "X-Acting-Role: ANALYST"

# Carol (analyst-only) requests triage on her own case.
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1009/triage \
  -H "X-API-Key: carol-key" -H "X-Acting-Role: ANALYST"

# Dave (approver-only) approves it.
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1009/approve \
  -H "X-API-Key: dave-key" -H "X-Acting-Role: APPROVER" \
  -H "Idempotency-Key: demo-approval-1" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVE","rationale":"Reviewed the evidence"}'

# Alice requests triage on her own case; her approver role is denied.
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/triage \
  -H "X-API-Key: alice-key" -H "X-Acting-Role: ANALYST"
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/approve \
  -H "X-API-Key: alice-key" -H "X-Acting-Role: APPROVER" \
  -H "Idempotency-Key: demo-approval-2" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVE","rationale":"Mine"}'   # 403

# Bob can approve Alice's case.
curl -s -X POST http://127.0.0.1:8000/v1/cases/CASE-1002/approve \
  -H "X-API-Key: bob-key" -H "X-Acting-Role: APPROVER" \
  -H "Idempotency-Key: demo-approval-3" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVE","rationale":"Independent review"}'

curl -s http://127.0.0.1:8000/v1/cases/CASE-1002/audit \
  -H "X-API-Key: alice-key" -H "X-Acting-Role: ANALYST"
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

Runs `ruff`, `pytest`, the 30-case golden eval and the end-to-end demo. The same command runs in GitHub Actions, together with a UI build job. The golden eval is a regression/acceptance gate, not a model-quality benchmark.

At the time of writing: **157 tests pass**, the golden eval is **30/30** with zero forbidden executions, and GitHub Actions is green (verify + UI jobs).

```bash
python scripts/demo.py
```

Runs the lifecycle without the UI.

### Known limitations

- The planner is deterministic, not an LLM. The exercise allows non-AI products; the AI-agent requirement is the development process. A real LLM can implement the same `Planner` protocol without changing the controls. The expansion path is in `evidence/design.md` and ADR-5.
- The executor is a mock; no real payment rails or money movement. Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. Production would use an outbox for exactly-once semantics.
- SQLite is embedded for zero-setup local run; it is single-writer and the executor/planner run in-process. Production uses a managed relational database, an outbox/queue for execution and a separate planner service (see "Scalability").
- API keys are synthetic; production would use the enterprise IdP/OAuth.
- One approval level; no multi-level approval chains.
- The slice's separation-of-duties check covers the case assignee and the human triage requester; production would also exclude proposal editors and beneficiaries (see "Production identity and AI expansion").
- AUD only; no FX or partial refunds.
- No Docker or cloud deployment; the production/CI-CD path is documented in `evidence/design.md`.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- The deterministic planner is trusted code. An untrusted or LLM planner should run out of process; the gateway keeps the repository private, but Python attribute access is not a sandbox.
- Chunked request bodies are rejected with 411; the request-size guard uses Content-Length. Production would enforce the limit at the API gateway/WAF.
- The UI has a TypeScript/Vite build check, a server-side smoke test and an asset-serving check, but no browser automation.
- No rate limiting; production would add it at the gateway.

### Production identity and AI expansion

The demo has four human identities: Alice and Bob each hold analyst + approver and can approve each other's cases; Carol is analyst-only; Dave is approver-only. Each identity has one key; the UI selects the active role. The slice records `triage_requested_by` and denies the assignee and the triage requester. Production would source the role set from the IdP, require an explicit active role, use a separate service identity for the agent/scheduler, and exclude every human who created, owns, requested triage for, proposed or edited the case from approving it — not just the assignee and requester. The production model is in `evidence/design.md` and ADR-2.

The deterministic planner is a stand-in. An LLM can implement the same `Planner` protocol behind the read-only tool gateway, with structured output, evidence grounding, confidence/abstain, deterministic fallback and the same service-side policy, approval, separation of duties and audit. The phased path, guardrails and what stays deterministic are in `evidence/design.md` and ADR-5. The LLM would propose; it would never execute or approve.

### Scalability

The local slice is a single-node SQLite app with synchronous execution; it is built for a demo, not throughput. The API is stateless per request and the constraints/idempotency are real, but SQLite has one writer and the executor/planner run in-process. The audit list is paginated (default 500, max 1000) and `audit_events(event_type)` is indexed; the rest of the production path is documented in `evidence/design.md` and ADR-3: managed relational DB, outbox + queue for execution, separate planner service, Redis/gateway rate limiting, aggregated metrics and immutable audit export. The queue/outbox and managed DB are intentionally out of the slice. A k6/Locust load test with concurrent triage/approval writers is the next step.

CAP: the decision/execution path is **CP** — under a partition or DB outage the API fails closed rather than accept a possibly-duplicate approval. Case lists, dashboards and audit export are **AP/eventually consistent**; the approval write re-checks the authoritative state and idempotency key, so a stale read cannot double-execute. Production uses a quorum DB, an outbox/queue with an idempotent executor and a separate planner service. See `evidence/design.md` and ADR-3.

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
