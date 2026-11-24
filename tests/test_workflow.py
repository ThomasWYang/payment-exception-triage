import pytest

from payment_triage.config import Identity, load_settings
from payment_triage.executor import MockPaymentExecutor
from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    ApproveRequest,
    Case,
    CaseStatus,
    CreateCaseRequest,
    ExceptionType,
    Payment,
    PaymentStatus,
    PolicyOutcome,
    ProposalDraft,
    Role,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.tools import ToolGateway
from payment_triage.workflow import Workflow, WorkflowError

AGENT = Identity("agent:triage-bot", Role.AGENT)
BOB = Identity("user:bob", Role.APPROVER)
ALICE_APPROVER = Identity("user:alice", Role.APPROVER)
ALICE_ANALYST = Identity("user:alice", Role.ANALYST)


def _payment(
    *,
    payment_id: str = "PAY-1",
    amount: int = 25000,
    status: PaymentStatus = PaymentStatus.FAILED,
    failure_code: str | None = "TEMPORARY_ERROR",
    duplicate_of: str | None = None,
) -> Payment:
    return Payment(
        id=payment_id,
        amount_cents=amount,
        status=status,
        failure_code=failure_code,
        duplicate_of=duplicate_of,
        account_id="ACC-1",
    )


def _seed_case(
    repo,
    *,
    payment: Payment,
    account_status: AccountStatus = AccountStatus.ACTIVE,
    exception_type: ExceptionType = ExceptionType.FAILED_PAYMENT,
    case_id: str = "CASE-1",
    assignee: str = "user:alice",
) -> Case:
    repo.create_account(Account(id="ACC-1", status=account_status))
    repo.create_payment(payment)
    case = Case(
        id=case_id,
        payment_id=payment.id,
        exception_type=exception_type,
        assignee=assignee,
    )
    repo.create_case(case)
    return case


def _workflow(repo, settings, *, planner=None, executor=None) -> Workflow:
    return Workflow(
        repo=repo,
        settings=settings,
        planner=planner or DeterministicPlanner(settings.refund_cap_cents),
        policy=PolicyEngine(settings),
        executor=executor or MockPaymentExecutor("mock"),
    )


def test_auto_retry_executes(repo, settings) -> None:
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, settings)
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.EXECUTED
    assert result.policy.outcome is PolicyOutcome.AUTO_APPROVED
    assert result.execution is not None
    assert result.execution.status.value == "SUCCEEDED"
    events = [event.event_type.value for event in wf.get_audit("CASE-1")]
    assert "PROPOSAL_SUBMITTED" in events
    assert "POLICY_APPROVED" in events
    assert "EXECUTION_SUCCEEDED" in events


def test_high_value_retry_pending_then_approve(repo, settings) -> None:
    _seed_case(repo, payment=_payment(amount=500000))
    wf = _workflow(repo, settings)
    triage = wf.triage("CASE-1", AGENT)
    assert triage.case.status is CaseStatus.PENDING_APPROVAL
    assert triage.policy.requires_approval is True
    result = wf.approve(
        "CASE-1",
        BOB,
        ApproveRequest(decision="APPROVE", rationale="looks good"),
        "key-1",
    )
    assert result.case.status is CaseStatus.EXECUTED
    assert result.execution is not None
    assert result.execution.status.value == "SUCCEEDED"
    assert result.approval.rationale_redacted == "looks good"


def test_refund_pending_then_approve(repo, settings) -> None:
    _seed_case(
        repo,
        payment=_payment(
            amount=150000,
            status=PaymentStatus.SETTLED,
            failure_code=None,
            duplicate_of="PAY-0",
        ),
        exception_type=ExceptionType.DUPLICATE_PAYMENT,
    )
    wf = _workflow(repo, settings)
    triage = wf.triage("CASE-1", AGENT)
    assert triage.case.status is CaseStatus.PENDING_APPROVAL
    result = wf.approve(
        "CASE-1",
        BOB,
        ApproveRequest(decision="APPROVE", rationale="duplicate confirmed"),
        "key-1",
    )
    assert result.case.status is CaseStatus.EXECUTED


def test_dispute_over_cap_escalates(repo, settings) -> None:
    _seed_case(
        repo,
        payment=_payment(
            amount=2_000_000, status=PaymentStatus.SETTLED, failure_code=None
        ),
        exception_type=ExceptionType.CUSTOMER_DISPUTE,
    )
    wf = _workflow(repo, settings)
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.ESCALATED
    assert result.execution is None
    assert "AMOUNT_EXCEEDS_LIMIT" in result.policy.reason_codes


def test_closed_account_escalates(repo, settings) -> None:
    _seed_case(repo, payment=_payment(), account_status=AccountStatus.CLOSED)
    wf = _workflow(repo, settings)
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.ESCALATED


def test_policy_violation_rejected(repo, settings) -> None:
    class BadPlanner:
        version = "bad-v1"

        def plan(self, case: Case, gateway: ToolGateway) -> ProposalDraft:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.REFUND_REQUEST,
                amount_cents=1000,
                rationale="invalid refund",
            )

    _seed_case(repo, payment=_payment(amount=1000))
    wf = _workflow(repo, settings, planner=BadPlanner())
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.REJECTED
    assert "ACTION_NOT_ALLOWED_FOR_EXCEPTION_TYPE" in result.policy.reason_codes
    assert "PAYMENT_NOT_SETTLED" in result.policy.reason_codes


def test_triage_twice_conflicts(repo, settings) -> None:
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, settings)
    wf.triage("CASE-1", AGENT)
    with pytest.raises(WorkflowError) as exc:
        wf.triage("CASE-1", AGENT)
    assert exc.value.code == "INVALID_STATE_TRANSITION"
    assert exc.value.http_status == 409


def test_triage_missing_case(repo, settings) -> None:
    wf = _workflow(repo, settings)
    with pytest.raises(WorkflowError) as exc:
        wf.triage("CASE-X", AGENT)
    assert exc.value.http_status == 404


def test_approve_wrong_role_denied(repo, settings) -> None:
    _seed_case(repo, payment=_payment(amount=500000))
    wf = _workflow(repo, settings)
    wf.triage("CASE-1", AGENT)
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1",
            ALICE_ANALYST,
            ApproveRequest(decision="APPROVE", rationale="x"),
            "key-1",
        )
    assert exc.value.code == "APPROVAL_DENIED"
    assert exc.value.http_status == 403
    events = [event.event_type.value for event in wf.get_audit("CASE-1")]
    assert "APPROVAL_DENIED" in events


def test_self_approval_denied(repo, settings) -> None:
    _seed_case(repo, payment=_payment(amount=500000))
    wf = _workflow(repo, settings)
    wf.triage("CASE-1", AGENT)
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1",
            ALICE_APPROVER,
            ApproveRequest(decision="APPROVE", rationale="x"),
            "key-1",
        )
    assert "SELF_APPROVAL_DENIED" in exc.value.message


def test_approve_wrong_state(repo, settings) -> None:
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, settings)
    with pytest.raises(WorkflowError) as exc:
        wf.approve(
            "CASE-1",
            BOB,
            ApproveRequest(decision="APPROVE", rationale="x"),
            "key-1",
        )
    assert exc.value.http_status == 409


def test_approve_reject(repo, settings) -> None:
    _seed_case(repo, payment=_payment(amount=500000))
    wf = _workflow(repo, settings)
    wf.triage("CASE-1", AGENT)
    result = wf.approve(
        "CASE-1",
        BOB,
        ApproveRequest(decision="REJECT", rationale="not now"),
        "key-1",
    )
    assert result.case.status is CaseStatus.REJECTED
    assert result.execution is None


def test_executor_failure_marks_failed(repo, settings) -> None:
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, settings, executor=MockPaymentExecutor("fail"))
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.FAILED
    assert result.execution is not None
    assert result.execution.status.value == "FAILED"
    assert result.execution.error_code == "MOCK_EXECUTOR_FAILURE"


def test_degraded_mode_requires_approval(repo, settings) -> None:
    degraded = load_settings(
        {
            "DEGRADED_MODE": "true",
            "AUTO_RETRY_LIMIT_CENTS": "100000",
            "REFUND_CAP_CENTS": "1000000",
        }
    )
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, degraded)
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.PENDING_APPROVAL
    assert "DEGRADED_MODE" in result.policy.reason_codes


def test_create_case_duplicate(repo, settings) -> None:
    repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
    repo.create_payment(_payment())
    wf = _workflow(repo, settings)
    request = CreateCaseRequest(
        payment_id="PAY-1", exception_type=ExceptionType.FAILED_PAYMENT
    )
    case = wf.create_case(request, ALICE_ANALYST)
    assert case.status is CaseStatus.OPEN
    with pytest.raises(WorkflowError) as exc:
        wf.create_case(request, ALICE_ANALYST)
    assert exc.value.code == "CASE_EXISTS"


def test_create_case_exception_mismatch(repo, settings) -> None:
    repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
    repo.create_payment(_payment())
    wf = _workflow(repo, settings)
    with pytest.raises(WorkflowError) as exc:
        wf.create_case(
            CreateCaseRequest(
                payment_id="PAY-1", exception_type=ExceptionType.DUPLICATE_PAYMENT
            ),
            ALICE_ANALYST,
        )
    assert exc.value.code == "EXCEPTION_TYPE_MISMATCH"


def test_case_view_and_list(repo, settings) -> None:
    _seed_case(repo, payment=_payment())
    wf = _workflow(repo, settings)
    wf.triage("CASE-1", AGENT)
    view = wf.get_case_view("CASE-1")
    assert view.proposal is not None
    assert view.execution is not None
    assert len(wf.list_case_views(None, 10, 0)) == 1
    assert len(wf.list_case_views(CaseStatus.EXECUTED, 10, 0)) == 1
    assert wf.status()["cases"]["EXECUTED"] == 1
