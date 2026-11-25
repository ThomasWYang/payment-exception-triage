import pytest
from pydantic import ValidationError

from payment_triage.models import (
    Action,
    ApproveRequest,
    Case,
    CaseStatus,
    CreateCaseRequest,
    ExceptionType,
    Payment,
    PaymentStatus,
    PolicyOutcome,
    Proposal,
    ProposalDraft,
    utcnow_iso,
)


def test_payment_requires_failure_code_when_failed() -> None:
    with pytest.raises(ValidationError):
        Payment(id="P1", amount_cents=100, status=PaymentStatus.FAILED, account_id="A1")


def test_payment_rejects_failure_code_when_settled() -> None:
    with pytest.raises(ValidationError):
        Payment(
            id="P1",
            amount_cents=100,
            status=PaymentStatus.SETTLED,
            failure_code="TEMPORARY_ERROR",
            account_id="A1",
        )


def test_payment_rejects_non_aud_and_non_positive_amounts() -> None:
    with pytest.raises(ValidationError):
        Payment(id="P1", amount_cents=0, status=PaymentStatus.SETTLED, account_id="A1")
    with pytest.raises(ValidationError):
        Payment(
            id="P1",
            amount_cents=100,
            currency="USD",
            status=PaymentStatus.SETTLED,
            account_id="A1",
        )


def test_proposal_amount_rules() -> None:
    with pytest.raises(ValidationError):
        Proposal(
            id="PR1",
            case_id="C1",
            action=Action.RETRY,
            rationale="x",
            planner_version="v1",
            policy_outcome=PolicyOutcome.AUTO_APPROVED,
            requires_approval=False,
        )
    with pytest.raises(ValidationError):
        Proposal(
            id="PR1",
            case_id="C1",
            action=Action.HOLD,
            amount_cents=100,
            rationale="x",
            planner_version="v1",
            policy_outcome=PolicyOutcome.AUTO_APPROVED,
            requires_approval=False,
        )
    proposal = Proposal(
        id="PR1",
        case_id="C1",
        action=Action.RETRY,
        amount_cents=100,
        rationale="x",
        planner_version="v1",
        policy_outcome=PolicyOutcome.AUTO_APPROVED,
        requires_approval=False,
    )
    assert proposal.amount_cents == 100


def test_approval_rationale_cannot_be_blank() -> None:
    with pytest.raises(ValidationError):
        ApproveRequest(decision="APPROVE", rationale="   ")


def test_case_defaults_and_timestamp() -> None:
    case = Case(
        id="C1",
        payment_id="P1",
        exception_type=ExceptionType.FAILED_PAYMENT,
        assignee="user:alice",
    )
    assert case.status is CaseStatus.OPEN
    assert case.triage_requested_by is None
    assert utcnow_iso().endswith("Z")


def test_case_records_triage_requester() -> None:
    case = Case(
        id="C1",
        payment_id="P1",
        exception_type=ExceptionType.FAILED_PAYMENT,
        assignee="user:alice",
        triage_requested_by="user:carol",
    )
    assert case.triage_requested_by == "user:carol"


def test_create_case_request_rejects_client_assignee() -> None:
    with pytest.raises(ValidationError):
        CreateCaseRequest(
            payment_id="P1",
            exception_type=ExceptionType.FAILED_PAYMENT,
            assignee="user:carol",
        )


def test_proposal_draft_amount_rules() -> None:
    with pytest.raises(ValidationError):
        ProposalDraft(payment_id="P1", action=Action.RETRY, rationale="x")
    with pytest.raises(ValidationError):
        ProposalDraft(
            payment_id="P1", action=Action.HOLD, amount_cents=100, rationale="x"
        )
