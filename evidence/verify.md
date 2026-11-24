# Verification

## Commands

- `python scripts/verify.py` — ruff + pytest + golden eval.
- `python scripts/demo.py` — end-to-end workflow demo.
- `python -m uvicorn payment_triage.main:app --reload` — local API (optional).

## Test matrix

Planned coverage:

- **Unit**: config validation; money/currency; tool allowlist/schema; planner mappings and abstain; each policy rule; role checks; separation of duties; state machine; redaction; idempotency.
- **Integration**: happy paths, approval, refund, escalation, 401/403/409, idempotency, executor failure, degraded mode, payload limits, audit immutability.
- **UI smoke**: `/ui/` 200, `/` redirect.
- **Golden eval**: 25–30 synthetic cases, exact expected outcomes.

## Results

TBD after implementation.

## Local vs CI

Both run `python scripts/verify.py`; GitHub Actions runs the same command on a clean runner.

## Known gaps

TBD after implementation.
