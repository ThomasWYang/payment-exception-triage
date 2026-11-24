"""Executor protocol and mock implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from payment_triage.models import Action, ExecutionStatus, Payment, PaymentStatus, Proposal


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    execution_ref: str | None = None
    error_code: str | None = None


class Executor(Protocol):
    def execute(self, proposal: Proposal, payment: Payment) -> ExecutionResult:
        """Execute an approved proposal. Must be idempotent at the workflow layer."""
        ...


class MockPaymentExecutor:
    """A deterministic stand-in for a payment processor; no money moves."""

    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode

    def execute(self, proposal: Proposal, payment: Payment) -> ExecutionResult:
        if self.mode == "fail":
            return ExecutionResult(
                status=ExecutionStatus.FAILED, error_code="MOCK_EXECUTOR_FAILURE"
            )
        if proposal.action is Action.ESCALATE:
            return ExecutionResult(
                status=ExecutionStatus.FAILED, error_code="EXECUTION_NOT_ALLOWED"
            )
        if proposal.action is Action.RETRY and payment.status is not PaymentStatus.FAILED:
            return ExecutionResult(
                status=ExecutionStatus.FAILED, error_code="PAYMENT_NOT_FAILED"
            )
        if (
            proposal.action is Action.REFUND_REQUEST
            and payment.status is not PaymentStatus.SETTLED
        ):
            return ExecutionResult(
                status=ExecutionStatus.FAILED, error_code="PAYMENT_NOT_SETTLED"
            )
        return ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            execution_ref=f"{proposal.action.value.lower()}-{proposal.id}",
        )
