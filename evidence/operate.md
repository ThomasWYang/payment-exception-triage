# Operate

## Run

```bash
python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

The demo (`python scripts/demo.py`) uses a temporary database and needs no setup. The API uses `var/payment_triage.db` by default; `env.example` documents overrides.

## Logs and status

- JSON logs to stdout: `ts`, `level`, `event`, `request_id`, `method`, `path`, `status_code`, `latency_ms`.
- Example: `{"ts":"...","level":"INFO","event":"request_completed","request_id":"...","method":"POST","path":"/v1/cases/CASE-1002/triage","status_code":200,"latency_ms":3.2}`
- `/healthz`: liveness.
- `/readyz`: DB readiness; 503 if the DB is unavailable.
- `/status`: version, mode (`normal` / `degraded` / `auto_approve_disabled`), uptime, cases by status, policy denials, executor failures, idempotent replays, approval denials, auth denials.
- Audit: `GET /v1/cases/{id}/audit`; append-only; UPDATE/DELETE are blocked by triggers.

## Configuration and secrets

All configuration is environment-based. `env.example` documents the synthetic defaults. No real secrets are committed. Production uses Secrets Manager/KMS and the enterprise IdP.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_PATH` | `var/payment_triage.db` | SQLite file |
| `REFUND_CAP_CENTS` | `1000000` | Refund cap |
| `AUTO_RETRY_LIMIT_CENTS` | `100000` | Auto-approval limit |
| `AUTO_APPROVE_ENABLED` | `true` | Kill switch for auto-approval |
| `DEGRADED_MODE` | `false` | Force manual approval |
| `EXECUTOR_MODE` | `mock` | `mock` or `fail` |
| `MAX_REQUEST_BYTES` | `65536` | Request size limit |
| `PLANNER_MAX_TOOL_CALLS` | `8` | Tool-call budget |
| `PLANNER_TIMEOUT_SECONDS` | `5` | Planner time budget |
| `API_KEY_*` | demo keys | Synthetic auth |
| `CORS_ORIGINS` | Vite origins | UI dev origins |

## Failure modes and runbook

| Symptom | Check | Action |
|---|---|---|
| `/readyz` 503 | DB file/path/permissions | Restore the DB; writes fail closed |
| Planner/tool error | `/status`, audit, logs | Case is ESCALATED; a human takes over; no action executed |
| Executor failure | execution `error_code`, audit | Case is FAILED; no automatic retry; requeue in a future worker |
| 401 | `X-API-Key` | Use a configured key |
| 403 | role / separation of duties | Use the right role; the approver must differ from the assignee |
| 409 | case state / idempotency key | Inspect the case and audit; use the same key to replay |
| 413 | `MAX_REQUEST_BYTES` | Reduce the payload |
| Invalid config at startup | process exit message | Fix the environment variable |
| Duplicate approval | audit `IDEMPOTENT_REPLAY` | Expected; one execution row |

## Rollback and degrade

- `AUTO_APPROVE_ENABLED=false`: no auto-approval; financial actions require approval.
- `DEGRADED_MODE=true`: `/status` shows `degraded`; financial actions require approval.
- Code rollback: redeploy the previous version; the schema is additive and `PRAGMA user_version` is set. Production would use expand/contract migrations.
- The executor is a mock; no money moves.

## Local to cloud

See `design.md`: API Gateway/ALB → ECS or Lambda → Aurora PostgreSQL; SQS/Step Functions for the workflow; S3 Object Lock for audit; KMS/Secrets Manager; CloudWatch/OpenTelemetry; GitHub Actions → ECR → Terraform/CDK; dev → staging → prod with approvals, canary and rollback.
