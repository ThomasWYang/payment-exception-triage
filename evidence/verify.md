# Verification

## Commands

- `python scripts/verify.py` — ruff + pytest + golden eval + end-to-end demo (the CI command).
- `python scripts/demo.py` — end-to-end workflow demo.
- `python -m uvicorn payment_triage.main:app --reload` — local API and the UI at `/ui/`.
- `cd ui && npm ci && npm run build` — rebuild the committed UI (Node 22+; not needed to run it).

## Results (2026-11-24, updated 2026-11-25)

- `ruff check .`: pass.
- `pytest`: 157 passed. The built UI is committed, so the UI smoke and asset-serving tests run; they skip only if `ui/dist` is removed.
- Golden eval: 30 cases; action 30/30, policy 30/30, status 30/30, reason codes 30/30; forbidden executions 0; idempotency replay pass; p95 triage latency ~2 ms locally.
- Demo: auto retry EXECUTED; high-value retry approved → EXECUTED; duplicate refund approved → EXECUTED; dispute over cap ESCALATED; closed account ESCALATED; self-approval DENIED; duplicate approval IDEMPOTENT (1 execution); multi-role approval EXECUTED (Alice approves Carol's case); single-role approval EXECUTED (Dave); triage requester DENIED (`TRIAGE_REQUESTER_CANNOT_APPROVE`); independent approval EXECUTED (Bob).
- Seed guide check: all ten seeded cases produce the outcomes in the README/UI sample table — 1001 and 1003 auto-execute; 1002, 1006, 1007, 1009 and 1010 wait for approval; 1004, 1005 and 1008 escalate. The owners are spread across all four humans: Alice 1002/1005/1008, Bob 1004/1010, Carol 1001/1009, Dave 1003/1006/1007; the seed test asserts the full owner map.
- React UI (primary demo surface): `npm run build` pass; `npm audit` 0 vulnerabilities; `GET /ui/` smoke test 200; CORS preflight test pass. The built `ui/dist` is committed, so the UI runs without Node. The user selector exposes Alice and Bob (analyst + approver), Carol (analyst) and Dave (approver); the active-role selector is explicit; Details shows the triage requester; the audit modal shows the first 500 events.
- Scalability guards: the audit response is paginated (default 500, max 1000; `offset`) and `audit_events(event_type)` is indexed for the `/status` counts. The remaining limits and the production path (managed DB, outbox/queue, separate planner service, Redis/gateway rate limits) are documented in `design.md`, ADR-3 and `operate.md`.
- Fresh public clone of the previous UI commit (`7e1fb76`): `scripts/verify.py` pass (122 passed); demo pass; UI smoke and asset-serving pass from the committed assets.
- Fresh public clone of the multi-role commit (`2cd1409`): `scripts/verify.py` pass (132 passed); demo pass (cross-approval, single-role approval, triage-requester denial); UI smoke and asset-serving pass.
- Fresh public clone of the scalability commit (`afab0eb`): `scripts/verify.py` pass (134 passed); demo pass; UI smoke and asset-serving pass.
- Fresh public clone of the seed-owner code commit (`0fdebb1`): `scripts/verify.py` pass (154 passed); demo pass; UI smoke and asset-serving pass.
- GitHub Actions: green, including the code-review fix commit (`45396278122`), the UI-primary commit (`45588435963`), the multi-role commit (`45596239039`), the scalability commit (`45597660315`), the final review commit (`45601828828`) and the seed owner commit (`45606250931`; verify + UI jobs).

## UI/README restructure (2026-11-25)

- The React UI is the primary demo surface; the built `ui/dist` is committed and served at `/ui/` with no Node install.
- The screen explains the three-step flow, roles, rules, live policy caps, sample cases and expected outcomes; Details and Audit show the proposal, evidence, triage requester, approval, execution and audit trail.
- A user selector and an active-role selector expose Alice and Bob (analyst + approver), Carol (analyst) and Dave (approver). Triage is offered when the active role is analyst; approvals when it is approver; the UI reuses one idempotency key per approval dialog.
- `ruff` + `pytest`: 157 passed; UI `npm run build`: pass; `npm audit`: 0 vulnerabilities.
- The new UI source was checked by the TypeScript/Vite build, the API tests and the asset-serving test; the earlier full-repo code review covered the backend and the original UI.
- The multi-role implementation is covered by config, model, repository, policy, workflow and API tests: role sets, active-role mismatch, Alice/Bob cross-approval, Carol's analyst-only denial, Dave's approver-only denial and the triage-requester denial. The seed spreads the ten cases across all four owners (Alice 1002/1005/1008, Bob 1004/1010, Carol 1001/1009, Dave 1003/1006/1007), and the seed test asserts the full owner map.
- Fresh public clone of the multi-role commit (`2cd1409`): verify pass (132 passed); demo pass; UI smoke and asset-serving pass.
- Fresh public clone of the seed-owner code commit (`0fdebb1`): verify pass (154 passed); demo pass; UI smoke and asset-serving pass.
- GitHub Actions: green for the multi-role commit (`2cd1409`, run `45596239039`) and the seed-owner code commit (`0fdebb1`, run `45606250931`; verify + UI jobs).

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
- UI reuses one idempotency key per approval dialog and gates approval actions to approver roles.
- CORS is outermost; 413/500 responses carry CORS headers; local API origins are allowed.
- `/readyz` probes the schema and does not create a database.
- Case reads use a deferred read transaction.
- Tool calls are logged; request completion is logged even on 500s.
- `env.example` documents all settings; type annotations were completed.

Deliberate dispositions:

- Idempotency keys are globally scoped per deployment; clients should use UUIDs (documented).
- Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. Production would use an outbox for exactly-once semantics.
- The UI has no browser automation; it is checked by a TypeScript/Vite build, a server-side smoke test and an asset-serving test, and the API endpoints it calls are covered by API tests.

### Final review (2026-11-25)

After the multi-role, scalability and CAP refactor, a final read-only review covered the backend/UI, the docs/requirements and the test gaps. The code review found six nits, all fixed: the Details outcome label, the approver active-role defense, the migration startup race, the audit actor role in the UI, the case-list pagination notice and the approval-modal error visibility. The final test/audit pass added the schema migration/index test (with old data), the default-active-role test, the non-approver active-role denial, REJECT SoD, denied-key reuse, active-role mismatch on triage/approve, denial side effects, concurrent different-key approvals, audit 404/boundaries, status modes/counters, the 500 envelope, an API/UI contract test, the seed idempotency test, the demo smoke test, the config key override, the policy active-role unit test and the executor truncation/non-draft planner paths. The docs audit fixed the Planner return type, LOG_LEVEL, demo docstring, ESCALATED labels, key wording, AGENT role and audit/log wording. The evidence pack was consolidated to five ADRs (the brief's 3-5 guidance). The seed now spreads the ten cases across all four owners so the assignee/triage-requester/approver combinations are visible; the seed test asserts the owner map. Final: 157 tests, the 30-case golden eval and the end-to-end demo. A second independent code review found eight nit-level items and a docs audit found four docs/UI nits; all were fixed: the unknown-route/method error envelope, non-finite timeout validation, the read-only `/readyz` probe, early-rejection logging, whitespace-key rejection and test-comment/docs/UI corrections.

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
| `2cd1409` multi-role identities, active roles, triage requester, four-user UI | 132 passed (CI run `45596239039`) |
| `afab0eb` audit pagination + event-type index | 134 passed (CI run `45597660315`) |
| `4d48923` final review fixes, coverage expansion, demo in verify | 154 passed (CI run `45601828828`) |
| `0fdebb1` seed owner matrix across all four humans | 154 passed (CI run `45606250931`) |
| current tree after the final review | 157 passed |

The scaffold and docs commits contain no code or tests.

## Mutation check

In a temporary worktree, the refund-cap rule was disabled (`if draft.amount_cents > self.settings.refund_cap_cents` → `if False`). The policy test `test_refund_over_cap_rejected`, the eval-gate test and the golden eval (G-012, G-029) failed as expected; the main history was unchanged. (The mutation check ran before the built UI was committed, so the UI smoke test was conditional in that worktree; the built UI is now tracked. That was not a mutation signal.)

## Test matrix

- **Unit**: config validation; role sets and active roles; money/currency; tool allowlist/schema/attempt budget; planner mappings and abstain; the main policy rules; role checks; separation of duties (assignee and triage requester); state machine; redaction; idempotency.
- **Integration**: happy paths, approval, refund, escalation, 401/403/409, multi-role cross-approval, triage requester denial, active-role mismatch, idempotency (including concurrent same-key), executor failure, degraded mode (retry integration; hold policy unit), payload limits, audit immutability (UPDATE/DELETE/REPLACE).
- **API**: role matrix (Carol analyst-only, Dave approver-only, Alice/Bob dual), active-role selection, validation, request ID, oversized/chunked bodies, not found, self-approval, idempotency, audit pagination (`limit`/`offset`, max 1000), CORS preflight, root redirect, readiness failure paths, missing-policy startup.
- **UI**: primary demo surface; four-user selector and active-role selector; TypeScript/Vite build; `GET /ui/` smoke test; every referenced hashed asset is fetched and checked; no browser automation.
- **E2E**: the `scripts/demo.py` lifecycle (now part of `scripts/verify.py`); the API end-to-end approval/cross-approval/triage-requester tests; the seed integration test (10 cases, Carol/Bob assignments); the golden eval.
- **Eval gate**: 30 golden cases plus an idempotency replay check.

## Local vs CI

| Check | Local | PR / pipeline |
|---|---|---|
| ruff | `python scripts/verify.py` | GitHub Actions `verify` |
| pytest | `python scripts/verify.py` | GitHub Actions `verify` |
| golden eval | `python scripts/verify.py` | GitHub Actions `verify` |
| demo (e2e) | `python scripts/verify.py` | GitHub Actions `verify` |
| UI build | `cd ui && npm run build` | GitHub Actions `ui` |
| container scan / SAST / dependency audit | not in slice | future pipeline gates |

## Known gaps

- The golden eval is a synthetic regression gate, not a model-quality benchmark.
- No real payment processor, LLM, IdP, multi-level approval or cloud deployment. The slice implements role sets, active roles and assignee/triage-requester SoD; production would add IdP, proposal editors/beneficiaries and an approval queue. The LLM expansion is design-only (`design.md`, ADR-5).
- SQLite and synthetic API keys are local-slice choices; the production mapping is documented.
- Execution is at-least-once at the workflow boundary; the executor must dedupe on the proposal ID. An outbox would provide exactly-once semantics in production.
- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- The deterministic planner is trusted code; an untrusted/LLM planner should run out of process.
- Chunked requests are rejected; production would enforce the size limit at the API gateway/WAF.
- The UI has a build check, a server-side smoke test and an asset-serving test, but no browser automation.
- SQLite is single-writer; execution runs inside the DB transaction; the planner uses a thread per triage; the case list is N+1; idempotency keys and audit rows grow without TTL/retention; `/status` counters are per process. The production path (managed DB, outbox/queue, separate planner service, Redis/gateway rate limits, aggregated metrics, audit export) is documented in `design.md`/ADR-3/`operate.md`. A load test is future work.
- CAP/distributed consistency is design-only: the slice is single-node. The target is CP for case/approval/execution and AP for case lists, metrics and audit export, with an outbox/queue and an idempotent executor (`design.md`, ADR-3).
- Rate limiting and background retries are future work.
