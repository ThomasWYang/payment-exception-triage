import sqlite3

import pytest

from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    AuditEvent,
    AuditEventType,
    Case,
    CaseStatus,
    ExceptionType,
    Execution,
    ExecutionStatus,
    Payment,
    PaymentStatus,
    PolicyOutcome,
    Proposal,
    Role,
    utcnow_iso,
)


def _account(account_id: str = "ACC-1", status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(id=account_id, status=status)


def _payment(
    payment_id: str = "PAY-1",
    status: PaymentStatus = PaymentStatus.FAILED,
    failure_code: str | None = "TEMPORARY_ERROR",
    amount: int = 1000,
) -> Payment:
    return Payment(
        id=payment_id,
        amount_cents=amount,
        status=status,
        failure_code=failure_code,
        account_id="ACC-1",
    )


def _case(case_id: str = "CASE-1", payment_id: str = "PAY-1") -> Case:
    return Case(
        id=case_id,
        payment_id=payment_id,
        exception_type=ExceptionType.FAILED_PAYMENT,
        assignee="user:alice",
    )


def _proposal(case_id: str = "CASE-1") -> Proposal:
    return Proposal(
        id="PROP-1",
        case_id=case_id,
        action=Action.RETRY,
        amount_cents=1000,
        rationale="retry",
        planner_version="v1",
        policy_outcome=PolicyOutcome.AUTO_APPROVED,
        requires_approval=False,
    )


def _audit(case_id: str = "CASE-1") -> AuditEvent:
    return AuditEvent(
        case_id=case_id,
        actor="agent:triage-bot",
        actor_role=Role.AGENT,
        event_type=AuditEventType.CASE_CREATED,
        outcome="SUCCESS",
        payload_hash="abc",
    )


def test_account_and_payment_roundtrip(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    account = repo.get_account("ACC-1")
    payment = repo.get_payment("PAY-1")
    assert account is not None and account.status is AccountStatus.ACTIVE
    assert payment is not None and payment.amount_cents == 1000


def test_case_roundtrip_and_unique_payment(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    case = repo.get_case("CASE-1")
    assert case is not None and case.status is CaseStatus.OPEN
    assert case.triage_requested_by is None
    assert repo.get_case_by_payment("PAY-1").id == "CASE-1"
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_case(
            Case(
                id="CASE-2",
                payment_id="PAY-1",
                exception_type=ExceptionType.FAILED_PAYMENT,
                assignee="user:alice",
            )
        )


def test_list_and_filter_cases(repo) -> None:
    repo.create_account(_account())
    for index in range(3):
        payment_id = f"PAY-{index}"
        repo.create_payment(_payment(payment_id=payment_id))
        repo.create_case(_case(case_id=f"CASE-{index}", payment_id=payment_id))
    repo.update_case_status("CASE-0", CaseStatus.OPEN, CaseStatus.PENDING_APPROVAL)
    assert len(repo.list_cases()) == 3
    assert len(repo.list_cases(CaseStatus.PENDING_APPROVAL)) == 1
    assert len(repo.list_cases(limit=1, offset=1)) == 1


def test_update_case_status_is_compare_and_set(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    assert repo.update_case_status("CASE-1", CaseStatus.OPEN, CaseStatus.PENDING_APPROVAL) is True
    assert repo.update_case_status("CASE-1", CaseStatus.OPEN, CaseStatus.EXECUTED) is False


def test_set_triage_requested_by(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    repo.set_triage_requested_by("CASE-1", "user:carol")
    case = repo.get_case("CASE-1")
    assert case is not None
    assert case.triage_requested_by == "user:carol"


def test_proposal_and_execution_uniqueness(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    repo.create_proposal(_proposal())
    proposal = repo.get_proposal_for_case("CASE-1")
    assert proposal is not None and proposal.action is Action.RETRY
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_proposal(
            Proposal(
                id="PROP-2",
                case_id="CASE-1",
                action=Action.HOLD,
                rationale="hold",
                planner_version="v1",
                policy_outcome=PolicyOutcome.AUTO_APPROVED,
                requires_approval=False,
            )
        )
    repo.create_execution(
        Execution(
            id="EX-1",
            proposal_id="PROP-1",
            status=ExecutionStatus.SUCCEEDED,
            execution_ref="ref-1",
        )
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_execution(
            Execution(id="EX-2", proposal_id="PROP-1", status=ExecutionStatus.SUCCEEDED)
        )


def test_audit_is_append_only(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    event_id = repo.append_audit(_audit())
    events = repo.list_audit("CASE-1")
    assert len(events) == 1
    assert events[0].id == event_id
    assert events[0].event_type is AuditEventType.CASE_CREATED
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute("UPDATE audit_events SET outcome = 'TAMPERED' WHERE id = ?", (event_id,))
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute("DELETE FROM audit_events WHERE id = ?", (event_id,))


def test_list_audit_pagination(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    first_id = repo.append_audit(_audit())
    second_id = repo.append_audit(_audit())
    first_page = repo.list_audit("CASE-1", limit=1, offset=0)
    second_page = repo.list_audit("CASE-1", limit=1, offset=1)
    assert [event.id for event in first_page] == [first_id]
    assert [event.id for event in second_page] == [second_id]


def test_idempotency_roundtrip(repo) -> None:
    repo.save_idempotency("key-1", "hash-1", 200, '{"ok":true}')
    stored = repo.get_idempotency("key-1")
    assert stored is not None and stored["request_hash"] == "hash-1"
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_idempotency("key-1", "hash-2", 200, "{}")


def test_status_counts_and_event_counts(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    repo.append_audit(_audit())
    counts = repo.status_counts()
    assert counts["OPEN"] == 1
    assert repo.count_events(AuditEventType.CASE_CREATED) == 1
    assert repo.count_events(AuditEventType.POLICY_REJECTED) == 0


def test_audit_replace_is_blocked(repo) -> None:
    repo.create_account(_account())
    repo.create_payment(_payment())
    repo.create_case(_case())
    event_id = repo.append_audit(_audit())
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute(
            """INSERT OR REPLACE INTO audit_events
               (id, case_id, actor, actor_role, event_type, outcome,
                reason_codes_json, payload_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                "CASE-1",
                "attacker",
                "AGENT",
                "CASE_CREATED",
                "TAMPERED",
                "[]",
                "x",
                utcnow_iso(),
            ),
        )


def test_list_cases_preserves_insertion_order(repo) -> None:
    repo.create_account(_account())
    for payment_id, case_id in [("PAY-A", "CASE-Z"), ("PAY-B", "CASE-A")]:
        repo.create_payment(_payment(payment_id=payment_id))
        repo.create_case(_case(case_id=case_id, payment_id=payment_id))
    assert [case.id for case in repo.list_cases()] == ["CASE-Z", "CASE-A"]
