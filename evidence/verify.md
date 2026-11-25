# Verification

## Commands

- `python scripts/verify.py` — ruff + pytest + golden eval (the CI command).
- `python scripts/demo.py` — end-to-end workflow demo.
- `python -m uvicorn payment_triage.main:app --reload` — local API and the UI at `/ui/`.
- `cd ui && npm ci && npm run build` — rebuild the committed UI (Node 22+; not needed to run it).

## Results (2026-11-24, updated 2026-11-25)

- `ruff check .`: pass.
- `pytest`: 122 passed. The built UI is committed, so the UI smoke and asset-serving tests run; they skip only if `ui/dist` is removed.
- Golden eval: 30 cases; action 30/30, policy 30/30, status 30/30, reason codes 30/30; forbidden executions 0; idempotency replay pass; p95 triage latency ~4 ms locally.
- Demo: auto retry EXECUTED; high-value retry approved → EXECUTED; duplicate refund approved → EXECUTED; dispute over cap ESCALATED; closed account ESCALATED; self-approval DENIED; duplicate approval IDEMPOTENT (1 execution).
- Seed guide check: all eight seeded cases produce the outcomes in the README/UI sample table — 1001 and 1003 auto-execute; 1002, 1006 and 1007 wait for approval; 1004, 1005 and 1008 escalate.
- React UI (primary demo surface): `npm run build` pass; `npm audit` 0 vulnerabilities; `GET /ui/` smoke test 200; CORS preflight test pass. The built `ui/dist` is committed, so the UI runs without Node.
- Fresh public clone of the final code commit (`7e1fb76`): `scripts/verify.py` pass (122 passed); demo pass; UI smoke and asset-serving pass from the committed assets.
- GitHub Actions: green, including the code-review fix commit (`45396278122`) and the UI-primary commit (`45588435963`, verify + UI jobs).

## UI/README restructure (2026-11-25)

- The React UI is the primary demo surface; the built `ui/dist` is committed and served at `/ui/` with no Node install.
- The screen explains the three-step flow, roles, rules, live policy caps, sample cases and expected outcomes; Details and Audit show the proposal, evidence, approval, execution and audit trail.
- Triage is offered to the analyst role; approvals to approver roles; the UI reuses one idempotency key per pending approval.
- `ruff` + `pytest`: 122 passed; UI `npm run build`: pass; `npm audit`: 0 vulnerabilities.
- The new UI source was checked by the TypeScript/Vite build, the API tests and the asset-serving test; the earlier full-repo code review covered the backend and the original UI.
- Fresh public clone of the pushed UI commit (`7e1fb76`): verify pass (122 passed); demo pass; UI smoke and asset-serving pass.
- GitHub Actions: green for the UI-primary commit (`45588435963`) and the UI role/asset commit (`45589701881`); the docs commit is checked by the same pipeline.

## Code-review hardening

The code-review skill reviewed the full repository and returned 27 findings (12 important, 15 nit). All valid findings were addressed, with regression tests where testable:

- Server-side assignee; clients cannot choose the assignee.
- Phone redaction covers `(04) …`, `04 …` and `+61 (0) …`.
- Chunked requests are rejected; `Content-Length` is required.
- `ProposalDraft` enforces the action/amount rules; invalid planner drafts escalate.
- Degraded mode gates holds as well as retries/refunds.
- Planner timeout is a real deadline (daemon worker); the request returns.
- Concurrent same-key approvals replay the stored result.
- Policy documents are validated at startup; a mid-flight policy failure escalates.
- Approval rationale is redacted and truncated to the field limit.
- The gateway keeps the repository private; tool attempts count toward the budget; args are revalidated.
- `recursive_triggers` blocks INSERT OR REPLACE on audit events.
- Refunds require an active account.
- UI reuses one idempotency key per pending approval and gates approval actions to approver roles.
- CORS is outermost; 413/500 responses carry CORS headers; local API origins are allowed.
- `/readyz` probes the schema and does not create a database.
- Case reads use a deferred read transaction.
- Tool calls are logged; request completion is logged even on 500s.
- `env.example` documents all settings; type annotations were completed.

Deliberate dispositions:

- Idempotency keys are globally scoped per deployment; clients should use UUIDs (documented).
- Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. Production would use an outbox for exactly-once semantics.
- The UI has no browser automation; it is checked by a TypeScript/Vite build, a server-side smoke test and an asset-serving test, and the API endpoints it calls are covered by API tests.

## Per-commit verification

Every code commit from the original build was checked out in an isolated worktree and its tests were run with `PYTHONPATH=<worktree>/src`:

| Commit | Tests |
|---|---|
| `6142f32` domain models, schema and repository | 26 passed |
| `7ce73c7` tool gateway | 31 passed |
| `b77a7b5` deterministic planner | 41 passed |
| `17db0c0` policy engine | 55 passed |
| `4f17631` workflow, audit, idempotency | 86 passed |
| `a0bf1de` API and operational endpoints | 95 passed |
| `95b731d` golden eval and verify script | 96 passed |
| `13b3193` demo | 96 passed |
| `74e23ed` UI primary + `/status` caps | 121 passed (CI run `45588435963`) |
| current tree after the UI role-gating fix, asset-serving test and README/evidence restructure | 122 passed |

The scaffold and docs commits contain no code or tests.

## Mutation check

In a temporary worktree, the refund-cap rule was disabled (`if draft.amount_cents > self.settings.refund_cap_cents` → `if False`). The policy test `test_refund_over_cap_rejected`, the eval-gate test and the golden eval (G-012, G-029) failed as expected; the main history was unchanged. (The mutation check ran before the built UI was committed, so the UI smoke test was conditional in that worktree; the built UI is now tracked. That was not a mutation signal.)

## Test matrix

- **Unit**: config validation; money/currency; tool allowlist/schema/attempt budget; planner mappings and abstain; each policy rule; role checks; separation of duties; state machine; redaction; idempotency.
- **Integration**: happy paths, approval, refund, escalation, 401/403/409, idempotency (including concurrent same-key), executor failure, degraded mode (retry and hold), payload limits, audit immutability (UPDATE/DELETE/REPLACE).
- **API**: role matrix, validation, request ID, oversized/chunked bodies, not found, self-approval, idempotency, CORS preflight, root redirect, readiness failure paths, missing-policy startup.
- **UI**: primary demo surface; TypeScript/Vite build; `GET /ui/` smoke test; every referenced hashed asset is fetched and checked; no browser automation.
- **Eval gate**: 30 golden cases plus an idempotency replay check.

## Local vs CI

| Check | Local | PR / pipeline |
|---|---|---|
| ruff | `python scripts/verify.py` | GitHub Actions `verify` |
| pytest | `python scripts/verify.py` | GitHub Actions `verify` |
| golden eval | `python scripts/verify.py` | GitHub Actions `verify` |
| UI build | `cd ui && npm run build` | GitHub Actions `ui` |
| container scan / SAST / dependency audit | not in slice | future pipeline gates |

## Known gaps

- The golden eval is a synthetic regression gate, not a model-quality benchmark.
- No real payment processor, LLM, IdP, multi-level approval or cloud deployment. The production multi-role/four-eye model and the LLM expansion path are documented in `design.md` (ADR-6/ADR-7); they are not implemented in the slice.
- SQLite and synthetic API keys are local-slice choices; the production mapping is documented.
- Execution is at-least-once at the workflow boundary; the executor must dedupe on the proposal ID. An outbox would provide exactly-once semantics in production.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- The deterministic planner is trusted code; an untrusted/LLM planner should run out of process.
- Chunked requests are rejected; production would enforce the size limit at the API gateway/WAF.
- The UI has a build check, a server-side smoke test and an asset-serving test, but no browser automation.
- Rate limiting and background retries are future work.
