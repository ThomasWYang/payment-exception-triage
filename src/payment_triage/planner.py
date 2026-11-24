"""Deterministic planner: evidence gathering and proposal only."""

from __future__ import annotations

from typing import Protocol

from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    Case,
    EvidenceRef,
    ExceptionType,
    FailureCode,
    Payment,
    PaymentStatus,
    ProposalDraft,
)
from payment_triage.tools import (
    AccountLookupResult,
    PaymentLookupResult,
    PolicySearchResult,
    ToolError,
    ToolGateway,
)


class Planner(Protocol):
    version: str

    def plan(self, case: Case, gateway: ToolGateway) -> ProposalDraft:
        """Gather evidence and propose an action. Never executes."""
        ...


class DeterministicPlanner:
    """A deterministic stand-in for an LLM agent.

    The planner can only read evidence and propose. It cannot execute or write.
    """

    version = "deterministic-v1"

    def __init__(self, refund_cap_cents: int) -> None:
        self.refund_cap_cents = refund_cap_cents

    def plan(self, case: Case, gateway: ToolGateway) -> ProposalDraft:
        try:
            payment_result = gateway.call("payment.lookup", {"payment_id": case.payment_id})
        except ToolError as exc:
            return _abstain(case, f"Payment lookup failed: {exc.code}", exc.code)
        assert isinstance(payment_result, PaymentLookupResult)
        payment = payment_result.payment

        try:
            account_result = gateway.call(
                "account.lookup", {"account_id": payment.account_id}
            )
        except ToolError as exc:
            return _abstain(case, f"Account lookup failed: {exc.code}", exc.code)
        assert isinstance(account_result, AccountLookupResult)
        account = account_result.account

        try:
            policy_result = gateway.call(
                "policy.search", {"query": _policy_query(case, payment), "limit": 3}
            )
        except ToolError as exc:
            return _abstain(case, f"Policy search failed: {exc.code}", exc.code)
        assert isinstance(policy_result, PolicySearchResult)

        evidence = [
            EvidenceRef(
                source="payment.lookup",
                ref=payment.id,
                summary=(
                    f"status={payment.status.value}, "
                    f"amount_cents={payment.amount_cents}"
                ),
            ),
            EvidenceRef(
                source="account.lookup",
                ref=account.id,
                summary=f"status={account.status.value}",
            ),
        ]
        evidence.extend(
            EvidenceRef(source="policy.search", ref=match.id, summary=match.title)
            for match in policy_result.matches
        )
        if not policy_result.matches:
            return _abstain(
                case,
                "No policy evidence found",
                "INSUFFICIENT_EVIDENCE",
                evidence,
            )

        draft = _decide(case, payment, account, self.refund_cap_cents)
        draft.evidence = evidence
        return draft


def _decide(
    case: Case, payment: Payment, account: Account, refund_cap_cents: int
) -> ProposalDraft:
    if case.exception_type is ExceptionType.FAILED_PAYMENT:
        if payment.status is not PaymentStatus.FAILED:
            return _abstain(
                case,
                "Failed-payment case has a non-failed payment",
                "INSUFFICIENT_EVIDENCE",
            )
        if payment.failure_code is FailureCode.TEMPORARY_ERROR:
            if account.status is AccountStatus.ACTIVE:
                return ProposalDraft(
                    payment_id=case.payment_id,
                    action=Action.RETRY,
                    amount_cents=payment.amount_cents,
                    rationale=(
                        f"Temporary error on {payment.id}; account {account.id} is active. "
                        "Retry proposed."
                    ),
                )
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.ESCALATE,
                rationale=(
                    f"Temporary error but account {account.id} is {account.status.value}. "
                    "Escalating instead of retrying."
                ),
                reason_codes=["ACCOUNT_NOT_ACTIVE"],
            )
        if payment.failure_code is FailureCode.INSUFFICIENT_FUNDS:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.HOLD,
                rationale=(
                    f"Insufficient funds on {payment.id}; hold the payment and notify "
                    "the customer."
                ),
                reason_codes=["INSUFFICIENT_FUNDS"],
            )
        if payment.failure_code is FailureCode.ACCOUNT_CLOSED:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.ESCALATE,
                rationale=f"Account {account.id} is closed; escalate to the account team.",
                reason_codes=["ACCOUNT_CLOSED"],
            )
        if payment.failure_code is FailureCode.LIMIT_EXCEEDED:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.ESCALATE,
                rationale=f"Payment {payment.id} exceeded a limit; escalate for review.",
                reason_codes=["LIMIT_EXCEEDED"],
            )
        return _abstain(case, "Failure code is missing", "INSUFFICIENT_EVIDENCE")

    if case.exception_type is ExceptionType.DUPLICATE_PAYMENT:
        if payment.status is PaymentStatus.SETTLED and payment.duplicate_of:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.REFUND_REQUEST,
                amount_cents=payment.amount_cents,
                rationale=(
                    f"{payment.id} is a settled duplicate of {payment.duplicate_of}; "
                    "refund proposed."
                ),
            )
        return _abstain(case, "Duplicate evidence is missing", "INSUFFICIENT_EVIDENCE")

    if case.exception_type is ExceptionType.CUSTOMER_DISPUTE:
        if payment.status is not PaymentStatus.SETTLED:
            return _abstain(case, "Dispute requires a settled payment", "INSUFFICIENT_EVIDENCE")
        if payment.amount_cents > refund_cap_cents:
            return ProposalDraft(
                payment_id=case.payment_id,
                action=Action.ESCALATE,
                rationale=(
                    f"Dispute amount {payment.amount_cents} exceeds the refund cap "
                    f"{refund_cap_cents}; escalate."
                ),
                reason_codes=["AMOUNT_EXCEEDS_LIMIT"],
            )
        return ProposalDraft(
            payment_id=case.payment_id,
            action=Action.REFUND_REQUEST,
            amount_cents=payment.amount_cents,
            rationale=f"Customer dispute on settled payment {payment.id}; refund proposed.",
        )

    return _abstain(case, "Unknown exception type", "INSUFFICIENT_EVIDENCE")


def _abstain(
    case: Case,
    rationale: str,
    code: str,
    evidence: list[EvidenceRef] | None = None,
) -> ProposalDraft:
    return ProposalDraft(
        payment_id=case.payment_id,
        action=Action.ESCALATE,
        rationale=rationale[:2000],
        evidence=evidence or [],
        reason_codes=[code],
    )


def _policy_query(case: Case, payment: Payment) -> str:
    parts = [
        case.exception_type.value,
        case.exception_type.value.replace("_", " "),
    ]
    if payment.failure_code is not None:
        parts.extend(
            [
                payment.failure_code.value,
                payment.failure_code.value.replace("_", " "),
            ]
        )
    return " ".join(parts)
