# Operate

## Run

```bash
python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

Open **http://127.0.0.1:8000/ui/** for the primary demo. The built UI is committed in `ui/dist`; Node 22+ is only needed to rebuild it (`cd ui && npm ci && npm run build`). The demo (`python scripts/demo.py`) uses a temporary database and needs no setup. The API uses `var/payment_triage.db` by default; `env.example` documents overrides.

## Logs and status

- JSON logs to stdout: `ts`, `level`, `event`, `request_id`, `method`, `path`, `status_code`, `latency_ms`.
- Example: `{"ts":"...","level":"INFO","event":"request_completed","request_id":"...","method":"POST","path":"/v1/cases/CASE-1002/triage","status_code":200,"latency_ms":3.2}`
- Tool calls are logged as `tool_called` events with the tool name, args hash, outcome and summary.
- `/healthz`: liveness.
- `/readyz`: DB file + schema readiness; 503 if the DB is missing or has no schema. It does not create a new DB.
- `/status`: version, mode (`normal` / `degraded` / `auto_approve_disabled`), uptime, cases by status, policy denials, executor failures, idempotent replays, approval denials, auth denials, and the live policy caps (`auto_retry_limit_cents`, `refund_cap_cents`, `auto_approve_enabled`, `degraded_mode`). The UI shows the mode in the status bar and the caps in the rules panel.
- Audit: `GET /v1/cases/{id}/audit`; append-only; UPDATE/DELETE/REPLACE are blocked by triggers.

## Configuration and secrets

All configuration is environment-based. `env.example` documents the synthetic defaults. No real secrets are committed. Production uses Secrets Manager/KMS and the enterprise IdP.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_PATH` | `var/payment_triage.db` | SQLite file |
| `POLICY_PATH` | `sample-data/policies.json` | Policy documents; validated at startup |
| `REFUND_CAP_CENTS` | `1000000` | Refund cap |
| `AUTO_RETRY_LIMIT_CENTS` | `100000` | Auto-approval limit |
| `AUTO_APPROVE_ENABLED` | `true` | Kill switch for auto-approval |
| `DEGRADED_MODE` | `false` | Force approval for retries, refunds and holds |
| `EXECUTOR_MODE` | `mock` | `mock` or `fail` |
| `MAX_REQUEST_BYTES` | `65536` | Request size limit |
| `PLANNER_MAX_TOOL_CALLS` | `8` | Tool-call budget (every attempt counts) |
| `PLANNER_TIMEOUT_SECONDS` | `5` | Planner deadline |
| `APP_VERSION` | `0.1.0` | Version shown by `/status` |
| `API_KEY_*` | demo keys | Synthetic identities: Alice (`ANALYST` + `APPROVER`), Bob (`ANALYST` + `APPROVER`), Carol (`ANALYST`), Dave (`APPROVER`), agent (`AGENT`) |
| `CORS_ORIGINS` | Vite + local API origins | UI origins |

An identity may hold multiple roles. `X-Acting-Role` selects the active role; the service records it and rejects a role the identity does not hold (`FORBIDDEN`), a role the endpoint does not allow (`ACTIVE_ROLE_MISMATCH`) or an unknown value (`INVALID_ACTING_ROLE`). If the header is omitted, the service uses the first role the identity holds that the endpoint allows.

## Failure modes and runbook

| Symptom | Check | Action |
|---|---|---|
| `/readyz` 503 | DB file/path/schema | Restore the DB; writes fail closed |
| Planner/tool error | `/status`, audit, logs | Case is ESCALATED; a human takes over; no action executed |
| Planner timeout | `/status`, audit | Case is ESCALATED; the daemon worker may still finish and be discarded |
| Executor failure | execution `error_code`, audit | Case is FAILED; no automatic retry; requeue in a future worker |
| Executor retry after DB failure | execution intent / audit | The executor must dedupe on the proposal ID; production adds an outbox |
| Missing/invalid policy | startup error, `/readyz` | Fix `POLICY_PATH`; startup fails fast, and a mid-flight failure escalates |
| 401 | `X-API-Key` | Use a configured key |
| 403 | role / active role / separation of duties | Use the right role and `X-Acting-Role`; the approver must differ from the assignee and the triage requester. Codes: `FORBIDDEN`, `ACTIVE_ROLE_MISMATCH`, `APPROVAL_DENIED` (`SELF_APPROVAL_DENIED`, `TRIAGE_REQUESTER_CANNOT_APPROVE`) |
| 409 | case state / idempotency key | Inspect the case and audit; use the same key to replay |
| 411 | `Transfer-Encoding: chunked` or missing `Content-Length` | Send a bounded body with `Content-Length` |
| 413 | `MAX_REQUEST_BYTES` | Reduce the payload |
| Invalid config at startup | process exit message | Fix the environment variable |
| Duplicate approval | audit `IDEMPOTENT_REPLAY` | Expected; one execution row |
| Idempotency key reused across cases | 409 `IDEMPOTENCY_CONFLICT` | Keys are globally scoped; use a UUID per request |
| Audit tamper attempt | `audit_events is append-only` | Expected; UPDATE/DELETE/REPLACE are blocked |
| `/ui/` 404 or blank | `ui/dist/index.html` exists | Rebuild the committed UI (`cd ui && npm ci && npm run build`) and commit `dist/`; the API serves only the built assets |

## Rollback and degrade

- `AUTO_APPROVE_ENABLED=false`: no auto-approval; retries, refunds and holds require approval.
- `DEGRADED_MODE=true`: `/status` shows `degraded`; retries, refunds and holds require approval.
- Code rollback: redeploy the previous version; the schema is additive and `PRAGMA user_version` is set. The UI is versioned with the API in the same repo, so a rollback restores the matching `ui/dist`. Production would use expand/contract migrations.
- The executor is a mock; no money moves.

## Local to cloud

See `design.md`: API Gateway/ALB → ECS or Lambda → Aurora PostgreSQL; SQS/Step Functions for the workflow; S3 Object Lock for audit; KMS/Secrets Manager; CloudWatch/OpenTelemetry; GitHub Actions → ECR → Terraform/CDK; dev → staging → prod with approvals, canary and rollback.
