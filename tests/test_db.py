from pathlib import Path

from payment_triage.db import connect, init_schema
from payment_triage.models import (
    Account,
    AccountStatus,
    AuditEvent,
    AuditEventType,
    Case,
    ExceptionType,
    Payment,
    PaymentStatus,
    Role,
)
from payment_triage.repository import Repository


def test_migration_adds_triage_requester_and_event_type_index(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    conn = connect(path)
    try:
        init_schema(conn)
        repo = Repository(conn)
        repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
        repo.create_payment(
            Payment(
                id="PAY-1",
                amount_cents=25000,
                status=PaymentStatus.FAILED,
                failure_code="TEMPORARY_ERROR",
                account_id="ACC-1",
            )
        )
        repo.create_case(
            Case(
                id="CASE-1",
                payment_id="PAY-1",
                exception_type=ExceptionType.FAILED_PAYMENT,
                assignee="user:alice",
            )
        )
        repo.append_audit(
            AuditEvent(
                case_id="CASE-1",
                actor="system:test",
                actor_role=Role.ANALYST,
                event_type=AuditEventType.CASE_CREATED,
                outcome="SUCCESS",
                payload_hash="abc",
            )
        )

        # Simulate a v2 database created before the multi-role migration.
        conn.execute("ALTER TABLE cases DROP COLUMN triage_requested_by")
        conn.execute("DROP INDEX IF EXISTS idx_audit_event_type")
        conn.execute("PRAGMA user_version = 2")

        init_schema(conn)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list('audit_events')")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        case = repo.get_case("CASE-1")
        audit_events = repo.list_audit("CASE-1")
        plan = " ".join(
            str(row[3])
            for row in conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT COUNT(*) FROM audit_events WHERE event_type = ?""",
                ("CASE_CREATED",),
            )
        )
    finally:
        conn.close()

    assert "triage_requested_by" in columns
    assert "idx_audit_event_type" in indexes
    assert version == 3
    assert case is not None
    assert case.triage_requested_by is None
    assert len(audit_events) == 1
    assert "idx_audit_event_type" in plan
