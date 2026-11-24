import threading

import pytest

from payment_triage.config import Identity
from payment_triage.db import connect
from payment_triage.executor import MockPaymentExecutor
from payment_triage.models import (
    Account,
    AccountStatus,
    ApproveRequest,
    Case,
    CaseStatus,
    ExceptionType,
    Payment,
    PaymentStatus,
    Role,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.repository import Repository
from payment_triage.workflow import Workflow, WorkflowError

AGENT = Identity("agent:triage-bot", Role.AGENT)
BOB = Identity("user:bob", Role.APPROVER)


def _pending_workflow(repo, settings, amount: int = 500000) -> Workflow:
    repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
    repo.create_payment(
        Payment(
            id="PAY-1",
            amount_cents=amount,
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
    wf = Workflow(
        repo=repo,
        settings=settings,
        planner=DeterministicPlanner(settings.refund_cap_cents),
        policy=PolicyEngine(settings),
        executor=MockPaymentExecutor("mock"),
    )
    wf.triage("CASE-1", AGENT)
    return wf


def test_same_key_replays(repo, settings) -> None:
    wf = _pending_workflow(repo, settings)
    request = ApproveRequest(decision="APPROVE", rationale="ok")
    first = wf.approve("CASE-1", BOB, request, "key-1")
    second = wf.approve("CASE-1", BOB, request, "key-1")
    assert first.case.status is CaseStatus.EXECUTED
    assert second.case.status is CaseStatus.EXECUTED
    row = repo.conn.execute("SELECT COUNT(*) AS n FROM executions").fetchone()
    assert row["n"] == 1
    events = [event.event_type.value for event in wf.get_audit("CASE-1")]
    assert "IDEMPOTENT_REPLAY" in events


def test_same_key_different_body_conflicts(repo, settings) -> None:
    wf = _pending_workflow(repo, settings)
    wf.approve(
        "CASE-1", BOB, ApproveRequest(decision="APPROVE", rationale="ok"), "key-1"
    )
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1",
            BOB,
            ApproveRequest(decision="REJECT", rationale="changed"),
            "key-1",
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc.value.http_status == 409


def test_different_key_after_execution_conflicts(repo, settings) -> None:
    wf = _pending_workflow(repo, settings)
    wf.approve(
        "CASE-1", BOB, ApproveRequest(decision="APPROVE", rationale="ok"), "key-1"
    )
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1", BOB, ApproveRequest(decision="APPROVE", rationale="ok"), "key-2"
        )
    assert exc.value.code == "INVALID_STATE_TRANSITION"
    assert exc.value.http_status == 409


def test_missing_key_rejected(repo, settings) -> None:
    wf = _pending_workflow(repo, settings)
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1", BOB, ApproveRequest(decision="APPROVE", rationale="ok"), None
        )
    assert exc.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    assert exc.value.http_status == 400


def test_concurrent_same_key_replays(repo, settings) -> None:
    wf = _pending_workflow(repo, settings)
    request = ApproveRequest(decision="APPROVE", rationale="ok")
    barrier = threading.Barrier(2)
    results: list[object] = [None, None]

    def call(index: int) -> None:
        conn = connect(settings.database_path)
        try:
            local = Workflow(
                repo=Repository(conn),
                settings=settings,
                planner=DeterministicPlanner(settings.refund_cap_cents),
                policy=PolicyEngine(settings),
                executor=MockPaymentExecutor("mock"),
            )
            barrier.wait()
            try:
                results[index] = local.approve("CASE-1", BOB, request, "same-key")
            except Exception as exc:  # noqa: BLE001 - captured for assertion
                results[index] = exc
        finally:
            conn.close()

    threads = [
        threading.Thread(target=call, args=(index,)) for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(not isinstance(result, Exception) for result in results), results
    for result in results:
        assert result.case.status is CaseStatus.EXECUTED  # type: ignore[attr-defined]
    row = repo.conn.execute("SELECT COUNT(*) AS n FROM executions").fetchone()
    assert row["n"] == 1
    audit = [event.event_type.value for event in wf.get_audit("CASE-1")]
    assert "IDEMPOTENT_REPLAY" in audit
