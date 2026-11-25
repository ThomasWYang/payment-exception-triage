"""Domain and API models for the payment exception triage service."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Role(str, Enum):
    AGENT = "AGENT"
    ANALYST = "ANALYST"
    APPROVER = "APPROVER"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class ExceptionType(str, Enum):
    FAILED_PAYMENT = "FAILED_PAYMENT"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    CUSTOMER_DISPUTE = "CUSTOMER_DISPUTE"


class PaymentStatus(str, Enum):
    FAILED = "FAILED"
    SETTLED = "SETTLED"


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"


class FailureCode(str, Enum):
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ACCOUNT_CLOSED = "ACCOUNT_CLOSED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


class Action(str, Enum):
    RETRY = "RETRY"
    HOLD = "HOLD"
    ESCALATE = "ESCALATE"
    REFUND_REQUEST = "REFUND_REQUEST"


class PolicyOutcome(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AuditEventType(str, Enum):
    CASE_CREATED = "CASE_CREATED"
    PROPOSAL_SUBMITTED = "PROPOSAL_SUBMITTED"
    POLICY_APPROVED = "POLICY_APPROVED"
    POLICY_REJECTED = "POLICY_REJECTED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    ESCALATED = "ESCALATED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    APPROVAL_DENIED = "APPROVAL_DENIED"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    status: AccountStatus


class Payment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)
    currency: Literal["AUD"] = "AUD"
    status: PaymentStatus
    failure_code: FailureCode | None = None
    duplicate_of: str | None = Field(default=None, max_length=64)
    account_id: str = Field(min_length=1, max_length=64)
    created_at: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _check_status_fields(self) -> "Payment":
        if self.status is PaymentStatus.FAILED and self.failure_code is None:
            raise ValueError("failure_code is required for a FAILED payment")
        if self.status is PaymentStatus.SETTLED and self.failure_code is not None:
            raise ValueError("failure_code must be null for a SETTLED payment")
        if self.duplicate_of is not None and self.status is not PaymentStatus.SETTLED:
            raise ValueError("duplicate_of is only valid for a SETTLED payment")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    payment_id: str = Field(min_length=1, max_length=64)
    exception_type: ExceptionType
    assignee: str = Field(min_length=1, max_length=128)
    triage_requested_by: str | None = Field(default=None, max_length=128)
    status: CaseStatus = CaseStatus.OPEN
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    ref: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=500)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    action: Action
    amount_cents: int | None = Field(default=None, gt=0)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    planner_version: str = Field(min_length=1, max_length=64)
    policy_outcome: PolicyOutcome
    reason_codes: list[str] = Field(default_factory=list)
    requires_approval: bool
    created_at: str = Field(default_factory=utcnow_iso)

    @model_validator(mode="after")
    def _check_amount(self) -> "Proposal":
        if self.action in {Action.RETRY, Action.REFUND_REQUEST} and self.amount_cents is None:
            raise ValueError("amount_cents is required for RETRY and REFUND_REQUEST")
        if self.action in {Action.HOLD, Action.ESCALATE} and self.amount_cents is not None:
            raise ValueError("amount_cents must be null for HOLD and ESCALATE")
        return self


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    proposal_id: str = Field(min_length=1, max_length=64)
    decision: ApprovalDecision
    approver: str = Field(min_length=1, max_length=128)
    rationale_redacted: str = Field(min_length=1, max_length=2000)
    created_at: str = Field(default_factory=utcnow_iso)


class Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    proposal_id: str = Field(min_length=1, max_length=64)
    status: ExecutionStatus
    execution_ref: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: str = Field(default_factory=utcnow_iso)


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    case_id: str | None = Field(default=None, max_length=64)
    actor: str = Field(min_length=1, max_length=128)
    actor_role: Role
    event_type: AuditEventType
    outcome: str = Field(min_length=1, max_length=64)
    reason_codes: list[str] = Field(default_factory=list)
    payload_hash: str = Field(min_length=1, max_length=128)
    created_at: str = Field(default_factory=utcnow_iso)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: PolicyOutcome
    reason_codes: list[str] = Field(default_factory=list)
    requires_approval: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ProposalDraft(BaseModel):
    """Internal planner output before policy evaluation."""

    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(min_length=1, max_length=64)
    action: Action
    amount_cents: int | None = Field(default=None, gt=0)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_amount(self) -> "ProposalDraft":
        if self.action in {Action.RETRY, Action.REFUND_REQUEST} and self.amount_cents is None:
            raise ValueError("amount_cents is required for RETRY and REFUND_REQUEST")
        if self.action in {Action.HOLD, Action.ESCALATE} and self.amount_cents is not None:
            raise ValueError("amount_cents must be null for HOLD and ESCALATE")
        return self


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(min_length=1, max_length=64)
    exception_type: ExceptionType


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rationale must not be blank")
        return value


class CaseView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Case
    proposal: Proposal | None = None
    approval: Approval | None = None
    execution: Execution | None = None


class TriageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Case
    proposal: Proposal
    policy: PolicyDecision
    execution: Execution | None = None


class ApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Case
    approval: Approval
    execution: Execution | None = None


class CaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CaseView]
    limit: int
    offset: int


class AuditList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEvent]
    limit: int = 500
    offset: int = 0


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail
