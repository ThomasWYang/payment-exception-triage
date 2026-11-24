# Operate

## Run

TBD — see the final README.

## Logs and status

- JSON logs to stdout.
- `/healthz` liveness; `/readyz` DB readiness; `/status` counters and mode.

## Configuration and secrets

All configuration is environment-based. `env.example` documents the synthetic demo values; no real secrets are committed. Production uses Secrets Manager/KMS.

## Failure modes and runbook

TBD after implementation.

## Rollback and degrade

- `AUTO_APPROVE_ENABLED=false` disables auto-approval.
- `DEGRADED_MODE=true` forces manual approval.
- Planner/tool failures escalate; executor failures mark the case FAILED.
- A DB outage returns 503; no unguarded execution.

## Local to cloud

See `design.md`.
