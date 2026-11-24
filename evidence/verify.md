# Verification

## Commands

- `python scripts/verify.py` — ruff + pytest + golden eval (the CI command).
- `python scripts/demo.py` — end-to-end workflow demo.
- `python -m uvicorn payment_triage.main:app --reload` — local API (optional).
- `cd ui && npm ci && npm run build` — optional React UI build.

## Results (2026-11-24)

- `ruff check .`: pass.
- `pytest`: 100 passed with the UI built; 99 passed + 1 skipped when `ui/dist` is absent (the UI smoke test is conditional).
- Golden eval: 30 cases; action 30/30, policy 30/30, status 30/30, reason codes 30/30; forbidden executions 0; idempotency replay pass; p95 triage latency ~1.8 ms.
- Demo: auto retry EXECUTED; high-value retry approved → EXECUTED; duplicate refund approved → EXECUTED; dispute over cap ESCALATED; closed account ESCALATED; self-approval DENIED; duplicate approval IDEMPOTENT (1 execution).
- React UI: `npm run build` pass; `npm audit` 0 vulnerabilities; `GET /ui/` smoke test 200; CORS preflight test pass.
- Fresh public clone: `scripts/verify.py` pass (99 passed + 1 skipped without the UI build); demo pass; UI build pass; UI smoke pass after the build.

## Per-commit verification

Every code commit was checked out in an isolated worktree and its tests were run with `PYTHONPATH=<worktree>/src`:

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
| current tree (UI, CORS) | 100 passed |

The scaffold and docs commits contain no code or tests.

## Mutation check

In a temporary worktree, the refund-cap rule was disabled (`if draft.amount_cents > self.settings.refund_cap_cents` → `if False`). The policy test `test_refund_over_cap_rejected`, the eval-gate test and the golden eval (G-012, G-029) failed as expected; the main history was unchanged. (The UI smoke test also failed in the worktree because the built UI is intentionally untracked; that is not a mutation signal.)

## Test matrix

- **Unit**: config validation; money/currency; tool allowlist/schema; planner mappings and abstain; each policy rule; role checks; separation of duties; state machine; redaction; idempotency.
- **Integration**: happy paths, approval, refund, escalation, 401/403/409, idempotency, executor failure, degraded mode, payload limits, audit immutability.
- **API**: role matrix, validation, request ID, oversized body, not found, self-approval, idempotency, CORS preflight, root redirect.
- **UI**: TypeScript/Vite build; `GET /ui/` server-side smoke test; no browser automation.
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
- No real payment processor, LLM, IdP, multi-level approval or cloud deployment.
- SQLite and synthetic API keys are local-slice choices; the production mapping is documented.
- The UI has a build check and a server-side smoke test, but no browser automation; the API endpoints it calls are covered by the API tests.
- Rate limiting and background retries are future work.
