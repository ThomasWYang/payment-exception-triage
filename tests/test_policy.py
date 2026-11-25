from payment_triage.config import Identity, load_settings
from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    Case,
    ExceptionType,
    Payment,
    PaymentStatus,
    PolicyOutcome,
    ProposalDraft,
    Role,
)
from payment_triage.policy import PolicyEngine


def _payment(
    *,
    status: PaymentStatus = PaymentStatus.FAILED,
    failure_code: str | None = "TEMPORARY_ERROR",
    amount: int = 25000,
    duplicate_of: str | None = None,
) -> Payment:
    return Payment(
        id="PAY-1",
        amount_cents=amount,
        status=status,
        failure_code=failure_code,
        duplicate_of=duplicate_of,
        account_id="ACC-1",
    )


def _account(status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(id="ACC-1", status=status)


def _case(
    exception_type: ExceptionType = ExceptionType.FAILED_PAYMENT,
    assignee: str = "user:alice",
    triage_requested_by: str | None = None,
) -> Case:
    return Case(
        id="CASE-1",
        payment_id="PAY-1",
        exception_type=exception_type,
        assignee=assignee,
        triage_requested_by=triage_requested_by,
    )


def _draft(
    action: Action,
    amount: int | None = 25000,
    payment_id: str = "PAY-1",
    reason_codes: list[str] | None = None,
) -> ProposalDraft:
    return ProposalDraft(
        payment_id=payment_id,
        action=action,
        amount_cents=amount,
        rationale="test proposal",
        reason_codes=reason_codes or [],
    )


def test_auto_retry_below_limit(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(), _payment(), _account(), _draft(Action.RETRY)
    )
    assert decision.outcome is PolicyOutcome.AUTO_APPROVED
    assert decision.requires_approval is False


def test_retry_above_limit_requires_approval(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(),
        _payment(amount=500000),
        _account(),
        _draft(Action.RETRY, amount=500000),
    )
    assert decision.outcome is PolicyOutcome.PENDING_APPROVAL
    assert decision.requires_approval is True
    assert "APPROVAL_REQUIRED" in decision.reason_codes


def test_refund_under_cap_requires_approval(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.DUPLICATE_PAYMENT),
        _payment(
            status=PaymentStatus.SETTLED,
            failure_code=None,
            amount=150000,
            duplicate_of="PAY-0",
        ),
        _account(),
        _draft(Action.REFUND_REQUEST, amount=150000),
    )
    assert decision.outcome is PolicyOutcome.PENDING_APPROVAL
    assert decision.requires_approval is True


def test_refund_over_cap_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.CUSTOMER_DISPUTE),
        _payment(status=PaymentStatus.SETTLED, failure_code=None, amount=2_000_000),
        _account(),
        _draft(Action.REFUND_REQUEST, amount=2_000_000),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "AMOUNT_EXCEEDS_LIMIT" in decision.reason_codes


def test_refund_over_original_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.CUSTOMER_DISPUTE),
        _payment(status=PaymentStatus.SETTLED, failure_code=None, amount=1000),
        _account(),
        _draft(Action.REFUND_REQUEST, amount=2000),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "AMOUNT_EXCEEDS_ORIGINAL" in decision.reason_codes


def test_refund_on_failed_payment_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.DUPLICATE_PAYMENT),
        _payment(amount=1000),
        _account(),
        _draft(Action.REFUND_REQUEST, amount=1000),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "PAYMENT_NOT_SETTLED" in decision.reason_codes


def test_retry_on_settled_payment_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(),
        _payment(status=PaymentStatus.SETTLED, failure_code=None),
        _account(),
        _draft(Action.RETRY),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "FAILURE_NOT_RETRYABLE" in decision.reason_codes


def test_retry_on_closed_account_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(),
        _payment(),
        _account(AccountStatus.CLOSED),
        _draft(Action.RETRY),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "ACCOUNT_CLOSED" in decision.reason_codes


def test_retry_amount_mismatch_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(), _payment(), _account(), _draft(Action.RETRY, amount=999)
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "RETRY_AMOUNT_MISMATCH" in decision.reason_codes


def test_hold_on_dispute_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.CUSTOMER_DISPUTE),
        _payment(status=PaymentStatus.SETTLED, failure_code=None),
        _account(),
        _draft(Action.HOLD, amount=None),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "ACTION_NOT_ALLOWED_FOR_EXCEPTION_TYPE" in decision.reason_codes


def test_payment_mismatch_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(), _payment(), _account(), _draft(Action.RETRY, payment_id="PAY-X")
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "PAYMENT_MISMATCH" in decision.reason_codes


def test_escalate_is_terminal_without_approval(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(),
        _payment(),
        _account(),
        _draft(Action.ESCALATE, amount=None, reason_codes=["INSUFFICIENT_EVIDENCE"]),
    )
    assert decision.outcome is PolicyOutcome.ESCALATED
    assert decision.requires_approval is False
    assert "INSUFFICIENT_EVIDENCE" in decision.reason_codes


def test_degraded_mode_forces_approval(settings) -> None:
    degraded = load_settings(
        {
            "DEGRADED_MODE": "true",
            "AUTO_RETRY_LIMIT_CENTS": "100000",
            "REFUND_CAP_CENTS": "1000000",
        }
    )
    decision = PolicyEngine(degraded).evaluate(
        _case(), _payment(), _account(), _draft(Action.RETRY)
    )
    assert decision.outcome is PolicyOutcome.PENDING_APPROVAL
    assert "DEGRADED_MODE" in decision.reason_codes


def test_degraded_mode_holds_for_approval(settings) -> None:
    degraded = load_settings(
        {
            "DEGRADED_MODE": "true",
            "AUTO_RETRY_LIMIT_CENTS": "100000",
            "REFUND_CAP_CENTS": "1000000",
        }
    )
    decision = PolicyEngine(degraded).evaluate(
        _case(),
        _payment(failure_code="INSUFFICIENT_FUNDS", amount=120000),
        _account(),
        _draft(Action.HOLD, amount=None),
    )
    assert decision.outcome is PolicyOutcome.PENDING_APPROVAL
    assert "DEGRADED_MODE" in decision.reason_codes


def test_refund_on_closed_account_rejected(settings) -> None:
    decision = PolicyEngine(settings).evaluate(
        _case(ExceptionType.DUPLICATE_PAYMENT),
        _payment(
            status=PaymentStatus.SETTLED,
            failure_code=None,
            amount=150000,
            duplicate_of="PAY-0",
        ),
        _account(AccountStatus.CLOSED),
        _draft(Action.REFUND_REQUEST, amount=150000),
    )
    assert decision.outcome is PolicyOutcome.REJECTED
    assert "ACCOUNT_CLOSED" in decision.reason_codes


def test_approver_checks(settings) -> None:
    engine = PolicyEngine(settings)
    case = _case()
    bob = Identity("user:bob", frozenset({Role.APPROVER}))
    bob_analyst = Identity("user:bob", frozenset({Role.ANALYST}))
    alice_approver = Identity("user:alice", frozenset({Role.APPROVER}))
    assert engine.check_approver(case, bob) == []
    assert "ROLE_NOT_PERMITTED" in engine.check_approver(case, bob_analyst)
    assert "SELF_APPROVAL_DENIED" in engine.check_approver(case, alice_approver)


def test_triage_requester_cannot_approve(settings) -> None:
    engine = PolicyEngine(settings)
    case = _case(assignee="user:alice", triage_requested_by="user:carol")
    carol_approver = Identity("user:carol", frozenset({Role.APPROVER}))
    reasons = engine.check_approver(case, carol_approver)
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in reasons
    assert "SELF_APPROVAL_DENIED" not in reasons
