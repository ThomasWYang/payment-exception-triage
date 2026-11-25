"""Pure policy and authorization rules."""

from __future__ import annotations

from payment_triage.config import Identity, Settings
from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    Case,
    ExceptionType,
    FailureCode,
    Payment,
    PaymentStatus,
    PolicyDecision,
    PolicyOutcome,
    ProposalDraft,
    Role,
)


class PolicyEngine:
    """Deterministic policy checks; no IO and no side effects."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self, case: Case, payment: Payment, account: Account, draft: ProposalDraft
    ) -> PolicyDecision:
        reasons: list[str] = []
        if draft.payment_id != case.payment_id:
            reasons.append("PAYMENT_MISMATCH")
        if not _action_compatible(case.exception_type, draft.action):
            reasons.append("ACTION_NOT_ALLOWED_FOR_EXCEPTION_TYPE")
        if draft.action is Action.RETRY:
            reasons.extend(self._check_retry(payment, account, draft))
        elif draft.action is Action.REFUND_REQUEST:
            reasons.extend(self._check_refund(payment, account, draft))

        details = {
            "action": draft.action.value,
            "amount_cents": draft.amount_cents,
            "auto_retry_limit_cents": self.settings.auto_retry_limit_cents,
            "refund_cap_cents": self.settings.refund_cap_cents,
            "degraded": self.settings.effective_degraded,
        }

        if reasons:
            return PolicyDecision(
                outcome=PolicyOutcome.REJECTED,
                reason_codes=reasons,
                requires_approval=False,
                details=details,
            )
        if draft.action is Action.ESCALATE:
            return PolicyDecision(
                outcome=PolicyOutcome.ESCALATED,
                reason_codes=list(draft.reason_codes),
                requires_approval=False,
                details=details,
            )
        if self.settings.effective_degraded and draft.action in {
            Action.RETRY,
            Action.REFUND_REQUEST,
            Action.HOLD,
        }:
            return PolicyDecision(
                outcome=PolicyOutcome.PENDING_APPROVAL,
                reason_codes=["DEGRADED_MODE", *draft.reason_codes],
                requires_approval=True,
                details=details,
            )
        if draft.action is Action.HOLD:
            return PolicyDecision(
                outcome=PolicyOutcome.AUTO_APPROVED,
                reason_codes=list(draft.reason_codes),
                requires_approval=False,
                details=details,
            )
        if draft.action is Action.REFUND_REQUEST:
            return PolicyDecision(
                outcome=PolicyOutcome.PENDING_APPROVAL,
                reason_codes=["APPROVAL_REQUIRED", *draft.reason_codes],
                requires_approval=True,
                details=details,
            )

        # RETRY
        if (
            draft.amount_cents is not None
            and draft.amount_cents <= self.settings.auto_retry_limit_cents
        ):
            return PolicyDecision(
                outcome=PolicyOutcome.AUTO_APPROVED,
                reason_codes=list(draft.reason_codes),
                requires_approval=False,
                details=details,
            )
        return PolicyDecision(
            outcome=PolicyOutcome.PENDING_APPROVAL,
            reason_codes=["APPROVAL_REQUIRED", *draft.reason_codes],
            requires_approval=True,
            details=details,
        )

    def _check_retry(
        self, payment: Payment, account: Account, draft: ProposalDraft
    ) -> list[str]:
        reasons: list[str] = []
        if payment.status is not PaymentStatus.FAILED:
            reasons.append("FAILURE_NOT_RETRYABLE")
        elif payment.failure_code is not FailureCode.TEMPORARY_ERROR:
            reasons.append("FAILURE_NOT_RETRYABLE")
        if account.status is AccountStatus.CLOSED:
            reasons.append("ACCOUNT_CLOSED")
        elif account.status is not AccountStatus.ACTIVE:
            reasons.append("ACCOUNT_NOT_ACTIVE")
        if draft.amount_cents is None:
            reasons.append("AMOUNT_REQUIRED")
        elif draft.amount_cents != payment.amount_cents:
            reasons.append("RETRY_AMOUNT_MISMATCH")
        return reasons

    def _check_refund(
        self, payment: Payment, account: Account, draft: ProposalDraft
    ) -> list[str]:
        reasons: list[str] = []
        if payment.status is not PaymentStatus.SETTLED:
            reasons.append("PAYMENT_NOT_SETTLED")
        if account.status is AccountStatus.CLOSED:
            reasons.append("ACCOUNT_CLOSED")
        elif account.status is not AccountStatus.ACTIVE:
            reasons.append("ACCOUNT_NOT_ACTIVE")
        if draft.amount_cents is None:
            reasons.append("AMOUNT_REQUIRED")
        else:
            if draft.amount_cents > payment.amount_cents:
                reasons.append("AMOUNT_EXCEEDS_ORIGINAL")
            if draft.amount_cents > self.settings.refund_cap_cents:
                reasons.append("AMOUNT_EXCEEDS_LIMIT")
        return reasons

    def check_approver(self, case: Case, identity: Identity) -> list[str]:
        reasons: list[str] = []
        if (
            Role.APPROVER not in identity.roles
            or identity.acting_role is not Role.APPROVER
        ):
            reasons.append("ROLE_NOT_PERMITTED")
        if identity.actor == case.assignee:
            reasons.append("SELF_APPROVAL_DENIED")
        if case.triage_requested_by is not None and identity.actor == case.triage_requested_by:
            reasons.append("TRIAGE_REQUESTER_CANNOT_APPROVE")
        return reasons


def _action_compatible(exception_type: ExceptionType, action: Action) -> bool:
    if action is Action.ESCALATE:
        return True
    if action is Action.HOLD:
        return exception_type is ExceptionType.FAILED_PAYMENT
    if action is Action.RETRY:
        return exception_type is ExceptionType.FAILED_PAYMENT
    if action is Action.REFUND_REQUEST:
        return exception_type in {
            ExceptionType.DUPLICATE_PAYMENT,
            ExceptionType.CUSTOMER_DISPUTE,
        }
    return False
