from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    Case,
    ExceptionType,
    Payment,
    PaymentStatus,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.tools import ToolGateway


def _setup(
    repo,
    *,
    payment: Payment,
    account_status: AccountStatus = AccountStatus.ACTIVE,
    exception_type: ExceptionType = ExceptionType.FAILED_PAYMENT,
    case_id: str = "CASE-1",
) -> Case:
    repo.create_account(Account(id="ACC-1", status=account_status))
    repo.create_payment(payment)
    case = Case(
        id=case_id,
        payment_id=payment.id,
        exception_type=exception_type,
        assignee="user:alice",
    )
    repo.create_case(case)
    return case


def _gateway(repo, settings, max_calls: int = 8) -> ToolGateway:
    return ToolGateway(repo, settings.policy_path, max_calls=max_calls)


def _planner(settings) -> DeterministicPlanner:
    return DeterministicPlanner(refund_cap_cents=settings.refund_cap_cents)


def test_temporary_error_active_retry(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=25000,
        status=PaymentStatus.FAILED,
        failure_code="TEMPORARY_ERROR",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.RETRY
    assert draft.amount_cents == 25000
    assert draft.payment_id == "PAY-1"
    assert draft.evidence


def test_temporary_error_closed_account_escalates(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=25000,
        status=PaymentStatus.FAILED,
        failure_code="TEMPORARY_ERROR",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment, account_status=AccountStatus.CLOSED)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.ESCALATE
    assert "ACCOUNT_NOT_ACTIVE" in draft.reason_codes


def test_insufficient_funds_holds(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=120000,
        status=PaymentStatus.FAILED,
        failure_code="INSUFFICIENT_FUNDS",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.HOLD


def test_account_closed_escalates(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=90000,
        status=PaymentStatus.FAILED,
        failure_code="ACCOUNT_CLOSED",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.ESCALATE


def test_limit_exceeded_escalates(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=300000,
        status=PaymentStatus.FAILED,
        failure_code="LIMIT_EXCEEDED",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.ESCALATE


def test_duplicate_payment_refund(repo, settings) -> None:
    payment = Payment(
        id="PAY-2",
        amount_cents=150000,
        status=PaymentStatus.SETTLED,
        duplicate_of="PAY-0",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment, exception_type=ExceptionType.DUPLICATE_PAYMENT)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.REFUND_REQUEST
    assert draft.amount_cents == 150000


def test_dispute_under_cap_refund(repo, settings) -> None:
    payment = Payment(
        id="PAY-3",
        amount_cents=250000,
        status=PaymentStatus.SETTLED,
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment, exception_type=ExceptionType.CUSTOMER_DISPUTE)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.REFUND_REQUEST


def test_dispute_over_cap_escalates(repo, settings) -> None:
    payment = Payment(
        id="PAY-4",
        amount_cents=2_000_000,
        status=PaymentStatus.SETTLED,
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment, exception_type=ExceptionType.CUSTOMER_DISPUTE)
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.ESCALATE
    assert "AMOUNT_EXCEEDS_LIMIT" in draft.reason_codes


def test_missing_payment_abstains(repo, settings) -> None:
    case = Case(
        id="CASE-X",
        payment_id="PAY-X",
        exception_type=ExceptionType.FAILED_PAYMENT,
        assignee="user:alice",
    )
    draft = _planner(settings).plan(case, _gateway(repo, settings))
    assert draft.action is Action.ESCALATE
    assert "NOT_FOUND" in draft.reason_codes


def test_tool_call_limit_abstains(repo, settings) -> None:
    payment = Payment(
        id="PAY-1",
        amount_cents=25000,
        status=PaymentStatus.FAILED,
        failure_code="TEMPORARY_ERROR",
        account_id="ACC-1",
    )
    case = _setup(repo, payment=payment)
    draft = _planner(settings).plan(case, _gateway(repo, settings, max_calls=1))
    assert draft.action is Action.ESCALATE
    assert "TOOL_CALL_LIMIT" in draft.reason_codes
