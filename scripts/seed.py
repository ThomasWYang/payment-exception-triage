"""Seed a local database with synthetic accounts, payments and cases."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from payment_triage.audit import record_audit
from payment_triage.config import load_settings
from payment_triage.db import connect, init_schema
from payment_triage.models import (
    Account,
    AuditEventType,
    Case,
    ExceptionType,
    Payment,
    Role,
)
from payment_triage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "sample-data"

CASE_TYPES = {
    "PAY-1001": ExceptionType.FAILED_PAYMENT,
    "PAY-1002": ExceptionType.FAILED_PAYMENT,
    "PAY-1003": ExceptionType.FAILED_PAYMENT,
    "PAY-1004": ExceptionType.FAILED_PAYMENT,
    "PAY-1005": ExceptionType.FAILED_PAYMENT,
    "PAY-1006": ExceptionType.DUPLICATE_PAYMENT,
    "PAY-1007": ExceptionType.CUSTOMER_DISPUTE,
    "PAY-1008": ExceptionType.CUSTOMER_DISPUTE,
    "PAY-1009": ExceptionType.FAILED_PAYMENT,
    "PAY-1010": ExceptionType.FAILED_PAYMENT,
}
CASE_ASSIGNEES = {"PAY-1009": "user:carol", "PAY-1010": "user:bob"}


def seed(database: Path, reset: bool = False) -> None:
    if reset:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database}{suffix}").unlink(missing_ok=True)

    conn = connect(database)
    try:
        init_schema(conn)
        repo = Repository(conn)
        accounts = json.loads((SAMPLE_DIR / "accounts.json").read_text(encoding="utf-8"))
        payments = json.loads((SAMPLE_DIR / "payments.json").read_text(encoding="utf-8"))

        for raw in accounts:
            try:
                repo.create_account(Account.model_validate(raw))
            except sqlite3.IntegrityError:
                pass

        for raw in payments:
            try:
                repo.create_payment(Payment.model_validate(raw))
            except sqlite3.IntegrityError:
                pass

        for raw in payments:
            exception_type = CASE_TYPES.get(raw["id"])
            if exception_type is None:
                continue
            case_id = f"CASE-{raw['id'].split('-')[1]}"
            created = False
            try:
                repo.create_case(
                    Case(
                        id=case_id,
                        payment_id=raw["id"],
                        exception_type=exception_type,
                        assignee=CASE_ASSIGNEES.get(raw["id"], "user:alice"),
                    )
                )
                created = True
            except sqlite3.IntegrityError:
                pass
            if created:
                record_audit(
                    repo,
                    case_id=case_id,
                    actor="system:seed",
                    role=Role.ANALYST,
                    event_type=AuditEventType.CASE_CREATED,
                    outcome="SUCCESS",
                    payload={"payment_id": raw["id"]},
                )

        print(
            f"Seeded {len(accounts)} accounts, {len(payments)} payments, "
            f"{len(CASE_TYPES)} cases into {database}"
        )
        print(f"Case statuses: {repo.status_counts()}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    seed(args.database or settings.database_path, reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
