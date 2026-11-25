"""Case workflow: state transitions, approval, execution and audit."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import uuid

from pydantic import ValidationError

from payment_triage.audit import payload_hash, record_audit
from payment_triage.config import Identity, Settings
from payment_triage.executor import ExecutionResult, Executor
from payment_triage.models import (
    Action,
    Approval,
    ApprovalDecision,
    ApprovalResult,
    ApproveRequest,
    AuditEvent,
    AuditEventType,
    Case,
    CaseStatus,
    CaseView,
    CreateCaseRequest,
    ExceptionType,
    Execution,
    ExecutionStatus,
    Payment,
    PaymentStatus,
    PolicyOutcome,
    Proposal,
    ProposalDraft,
    TriageView,
)
from payment_triage.planner import Planner
from payment_triage.policy import PolicyEngine
from payment_triage.redaction import redact_pii
from payment_triage.repository import Repository
from payment_triage.tools import ToolGateway

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    """A controlled workflow failure with an HTTP mapping."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        reason_codes: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.reason_codes = reason_codes or []


class Workflow:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        planner: Planner,
        policy: PolicyEngine,
        executor: Executor,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.planner = planner
        self.policy = policy
        self.executor = executor

    # Case creation ------------------------------------------------------------

    def create_case(self, request: CreateCaseRequest, identity: Identity) -> Case:
        payment = self.repo.get_payment(request.payment_id)
        if payment is None:
            raise WorkflowError("PAYMENT_NOT_FOUND", "Payment not found", 404)
        expected = (
            PaymentStatus.FAILED
            if request.exception_type is ExceptionType.FAILED_PAYMENT
            else PaymentStatus.SETTLED
        )
        if payment.status is not expected:
            raise WorkflowError(
                "EXCEPTION_TYPE_MISMATCH",
                "Exception type does not match the payment status",
                400,
            )
        if self.repo.get_case_by_payment(payment.id) is not None:
            raise WorkflowError(
                "CASE_EXISTS", "A case already exists for this payment", 409
            )
        case = Case(
            id=_new_id("CASE"),
            payment_id=payment.id,
            exception_type=request.exception_type,
            assignee=identity.actor,
        )
        with self.repo.transaction():
            try:
                self.repo.create_case(case)
            except sqlite3.IntegrityError as exc:
                raise WorkflowError(
                    "CASE_EXISTS", "A case already exists for this payment", 409
                ) from exc
            record_audit(
                self.repo,
                case_id=case.id,
                actor=identity.actor,
                role=identity.acting_role,
                event_type=AuditEventType.CASE_CREATED,
                outcome="SUCCESS",
                payload={
                    "payment_id": payment.id,
                    "exception_type": case.exception_type.value,
                },
            )
        return case

    # Triage -------------------------------------------------------------------

    def triage(self, case_id: str, identity: Identity) -> TriageView:
        case = self.repo.get_case(case_id)
        if case is None:
            raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)
        if case.status is not CaseStatus.OPEN:
            raise WorkflowError(
                "INVALID_STATE_TRANSITION", f"Case is {case.status.value}", 409
            )
        payment = self.repo.get_payment(case.payment_id)
        if payment is None:
            raise WorkflowError("PAYMENT_NOT_FOUND", "Payment not found", 404)
        account = self.repo.get_account(payment.account_id)
        if account is None:
            raise WorkflowError("ACCOUNT_NOT_FOUND", "Account not found", 500)

        try:
            gateway = ToolGateway(
                self.repo,
                self.settings.policy_path,
                max_calls=self.settings.planner_max_tool_calls,
            )
        except Exception:  # noqa: BLE001 - fail safe, escalate
            logger.exception("tool gateway init failed", extra={"case_id": case.id})
            draft = _escalation_draft(
                case, "TOOL_INIT_ERROR", "Policy/tool configuration failed"
            )
        else:
            draft = self._plan(case, gateway)
            for call in gateway.calls:
                logger.info(
                    "tool_called",
                    extra={
                        "case_id": case.id,
                        "tool": call.name,
                        "args_hash": call.args_hash,
                        "outcome": call.outcome,
                        "summary": call.summary,
                    },
                )
        decision = self.policy.evaluate(case, payment, account, draft)
        try:
            proposal = Proposal(
                id=_new_id("PROP"),
                case_id=case.id,
                action=draft.action,
                amount_cents=draft.amount_cents,
                rationale=draft.rationale,
                evidence=draft.evidence,
                planner_version=self.planner.version,
                policy_outcome=decision.outcome,
                reason_codes=decision.reason_codes,
                requires_approval=decision.requires_approval,
            )
        except ValidationError:
            logger.exception("invalid planner draft", extra={"case_id": case.id})
            draft = _escalation_draft(
                case, "PLANNER_ERROR", "Planner produced an invalid proposal"
            )
            decision = self.policy.evaluate(case, payment, account, draft)
            proposal = Proposal(
                id=_new_id("PROP"),
                case_id=case.id,
                action=draft.action,
                amount_cents=draft.amount_cents,
                rationale=draft.rationale,
                evidence=draft.evidence,
                planner_version=self.planner.version,
                policy_outcome=decision.outcome,
                reason_codes=decision.reason_codes,
                requires_approval=decision.requires_approval,
            )

        with self.repo.transaction():
            current = self.repo.get_case(case.id)
            if current is None or current.status is not CaseStatus.OPEN:
                raise WorkflowError(
                    "INVALID_STATE_TRANSITION", "Case is no longer open", 409
                )
            self.repo.set_triage_requested_by(case.id, identity.actor)
            try:
                self.repo.create_proposal(proposal)
            except sqlite3.IntegrityError as exc:
                raise WorkflowError(
                    "INVALID_STATE_TRANSITION", "Case already has a proposal", 409
                ) from exc
            record_audit(
                self.repo,
                case_id=case.id,
                actor=identity.actor,
                role=identity.acting_role,
                event_type=AuditEventType.PROPOSAL_SUBMITTED,
                outcome="SUCCESS",
                reason_codes=decision.reason_codes,
                payload={
                    "proposal_id": proposal.id,
                    "action": proposal.action.value,
                    "amount_cents": proposal.amount_cents,
                    "policy_outcome": decision.outcome.value,
                    "triage_requested_by": identity.actor,
                },
            )
            execution: Execution | None = None
            if decision.outcome is PolicyOutcome.REJECTED:
                self._transition(case.id, CaseStatus.OPEN, CaseStatus.REJECTED)
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.POLICY_REJECTED,
                    outcome="REJECTED",
                    reason_codes=decision.reason_codes,
                    payload={"proposal_id": proposal.id},
                )
            elif decision.outcome is PolicyOutcome.ESCALATED:
                self._transition(case.id, CaseStatus.OPEN, CaseStatus.ESCALATED)
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.ESCALATED,
                    outcome="ESCALATED",
                    reason_codes=decision.reason_codes,
                    payload={"proposal_id": proposal.id},
                )
            elif decision.outcome is PolicyOutcome.PENDING_APPROVAL:
                self._transition(case.id, CaseStatus.OPEN, CaseStatus.PENDING_APPROVAL)
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.APPROVAL_REQUESTED,
                    outcome="PENDING",
                    reason_codes=decision.reason_codes,
                    payload={"proposal_id": proposal.id},
                )
            else:
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.POLICY_APPROVED,
                    outcome="AUTO_APPROVED",
                    reason_codes=decision.reason_codes,
                    payload={"proposal_id": proposal.id},
                )
                execution = self._execute(proposal, payment, case, identity)
                new_status = (
                    CaseStatus.EXECUTED
                    if execution.status is ExecutionStatus.SUCCEEDED
                    else CaseStatus.FAILED
                )
                self._transition(case.id, CaseStatus.OPEN, new_status)
            updated = self.repo.get_case(case.id)
            if updated is None:
                raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)
            return TriageView(
                case=updated, proposal=proposal, policy=decision, execution=execution
            )

    # Approval -----------------------------------------------------------------

    def approve(
        self,
        case_id: str,
        identity: Identity,
        request: ApproveRequest,
        idempotency_key: str | None,
    ) -> ApprovalResult:
        key = (idempotency_key or "").strip()
        if not key:
            raise WorkflowError(
                "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required", 400
            )
        case = self.repo.get_case(case_id)
        if case is None:
            raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)

        violations = self.policy.check_approver(case, identity)
        if violations:
            with self.repo.transaction():
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.APPROVAL_DENIED,
                    outcome="DENIED",
                    reason_codes=violations,
                    payload={"decision": request.decision.value},
                )
            raise WorkflowError(
                "APPROVAL_DENIED",
                ", ".join(violations),
                403,
                reason_codes=violations,
            )

        request_hash = payload_hash(
            {
                "case_id": case.id,
                "decision": request.decision.value,
                "rationale": request.rationale,
                "approver": identity.actor,
            }
        )
        existing = self.repo.get_idempotency(key)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise WorkflowError(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency key was reused with a different request",
                    409,
                )
            with self.repo.transaction():
                record_audit(
                    self.repo,
                    case_id=case.id,
                    actor=identity.actor,
                    role=identity.acting_role,
                    event_type=AuditEventType.IDEMPOTENT_REPLAY,
                    outcome="REPLAY",
                    payload={"idempotency_key": key},
                )
            return ApprovalResult.model_validate_json(str(existing["response_json"]))

        if case.status is not CaseStatus.PENDING_APPROVAL:
            raise WorkflowError(
                "INVALID_STATE_TRANSITION", f"Case is {case.status.value}", 409
            )
        proposal = self.repo.get_proposal_for_case(case.id)
        if proposal is None:
            raise WorkflowError("PROPOSAL_NOT_FOUND", "Proposal not found", 409)
        payment = self.repo.get_payment(case.payment_id)
        if payment is None:
            raise WorkflowError("PAYMENT_NOT_FOUND", "Payment not found", 404)

        approval = Approval(
            id=_new_id("APPR"),
            case_id=case.id,
            proposal_id=proposal.id,
            decision=request.decision,
            approver=identity.actor,
            rationale_redacted=redact_pii(request.rationale)[:2000],
        )
        try:
            with self.repo.transaction():
                current = self.repo.get_case(case.id)
                if current is None or current.status is not CaseStatus.PENDING_APPROVAL:
                    replay = self.repo.get_idempotency(key)
                    if replay is not None and replay["request_hash"] == request_hash:
                        record_audit(
                            self.repo,
                            case_id=case.id,
                            actor=identity.actor,
                            role=identity.acting_role,
                            event_type=AuditEventType.IDEMPOTENT_REPLAY,
                            outcome="REPLAY",
                            payload={"idempotency_key": key},
                        )
                        return ApprovalResult.model_validate_json(
                            str(replay["response_json"])
                        )
                    raise WorkflowError(
                        "INVALID_STATE_TRANSITION", "Case is no longer pending", 409
                    )
                try:
                    self.repo.create_approval(approval)
                except sqlite3.IntegrityError as exc:
                    raise WorkflowError(
                        "ALREADY_APPROVED", "Proposal already has an approval", 409
                    ) from exc
                execution: Execution | None = None
                if request.decision is ApprovalDecision.REJECT:
                    self._transition(
                        case.id, CaseStatus.PENDING_APPROVAL, CaseStatus.REJECTED
                    )
                    record_audit(
                        self.repo,
                        case_id=case.id,
                        actor=identity.actor,
                        role=identity.acting_role,
                        event_type=AuditEventType.REJECTED,
                        outcome="REJECTED",
                        reason_codes=["APPROVER_REJECTED"],
                        payload={
                            "approval_id": approval.id,
                            "proposal_id": proposal.id,
                        },
                    )
                else:
                    record_audit(
                        self.repo,
                        case_id=case.id,
                        actor=identity.actor,
                        role=identity.acting_role,
                        event_type=AuditEventType.APPROVED,
                        outcome="APPROVED",
                        reason_codes=["APPROVER_APPROVED"],
                        payload={
                            "approval_id": approval.id,
                            "proposal_id": proposal.id,
                        },
                    )
                    execution = self._execute(proposal, payment, case, identity)
                    new_status = (
                        CaseStatus.EXECUTED
                        if execution.status is ExecutionStatus.SUCCEEDED
                        else CaseStatus.FAILED
                    )
                    self._transition(
                        case.id, CaseStatus.PENDING_APPROVAL, new_status
                    )
                updated = self.repo.get_case(case.id)
                if updated is None:
                    raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)
                result = ApprovalResult(
                    case=updated, approval=approval, execution=execution
                )
                self.repo.save_idempotency(
                    key, request_hash, 200, result.model_dump_json()
                )
                return result
        except sqlite3.IntegrityError as exc:
            replay = self.repo.get_idempotency(key)
            if replay is not None and replay["request_hash"] == request_hash:
                return ApprovalResult.model_validate_json(str(replay["response_json"]))
            raise WorkflowError(
                "IDEMPOTENCY_CONFLICT", "Concurrent request with the same key", 409
            ) from exc

    # Reads --------------------------------------------------------------------

    def get_case_view(self, case_id: str) -> CaseView:
        with self.repo.read_transaction():
            case = self.repo.get_case(case_id)
            if case is None:
                raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)
            proposal = self.repo.get_proposal_for_case(case.id)
            approval = (
                self.repo.get_approval_for_proposal(proposal.id) if proposal else None
            )
            execution = (
                self.repo.get_execution_for_proposal(proposal.id) if proposal else None
            )
            return CaseView(
                case=case, proposal=proposal, approval=approval, execution=execution
            )

    def list_case_views(
        self, status: CaseStatus | None, limit: int, offset: int
    ) -> list[CaseView]:
        cases = self.repo.list_cases(status, limit=limit, offset=offset)
        return [self.get_case_view(case.id) for case in cases]

    def get_audit(
        self, case_id: str, limit: int = 500, offset: int = 0
    ) -> list[AuditEvent]:
        if self.repo.get_case(case_id) is None:
            raise WorkflowError("CASE_NOT_FOUND", "Case not found", 404)
        return self.repo.list_audit(case_id, limit=limit, offset=offset)

    def status(self) -> dict[str, object]:
        return {
            "cases": self.repo.status_counts(),
            "policy_denials": self.repo.count_events(AuditEventType.POLICY_REJECTED),
            "executor_failures": self.repo.count_events(
                AuditEventType.EXECUTION_FAILED
            ),
            "idempotent_replays": self.repo.count_events(
                AuditEventType.IDEMPOTENT_REPLAY
            ),
            "approval_denials": self.repo.count_events(
                AuditEventType.APPROVAL_DENIED
            ),
        }

    # Internals ----------------------------------------------------------------

    def _plan(self, case: Case, gateway: ToolGateway) -> ProposalDraft:
        result: queue.Queue[tuple[str, object]] = queue.Queue()

        def worker() -> None:
            try:
                result.put(("ok", self.planner.plan(case, gateway)))
            except Exception as exc:  # noqa: BLE001 - fail safe, never execute
                result.put(("error", exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(self.settings.planner_timeout_seconds)
        if thread.is_alive():
            return _escalation_draft(
                case, "PLANNER_TIMEOUT", "Planner exceeded the time limit"
            )
        try:
            status, payload = result.get_nowait()
        except queue.Empty:
            return _escalation_draft(case, "PLANNER_ERROR", "Planner returned no result")
        if status == "error":
            logger.error("planner failed: %s", payload, extra={"case_id": case.id})
            return _escalation_draft(case, "PLANNER_ERROR", "Planner failed")
        if not isinstance(payload, ProposalDraft):
            logger.error("planner returned an invalid draft", extra={"case_id": case.id})
            return _escalation_draft(
                case, "PLANNER_ERROR", "Planner returned an invalid draft"
            )
        return payload

    def _transition(
        self, case_id: str, expected: CaseStatus, new: CaseStatus
    ) -> None:
        if not self.repo.update_case_status(case_id, expected, new):
            raise WorkflowError(
                "INVALID_STATE_TRANSITION",
                f"Case is no longer {expected.value}",
                409,
            )

    def _execute(
        self, proposal: Proposal, payment: Payment, case: Case, identity: Identity
    ) -> Execution:
        existing = self.repo.get_execution_for_proposal(proposal.id)
        if existing is not None:
            return existing
        try:
            result = self.executor.execute(proposal, payment, proposal.id)
        except Exception:  # noqa: BLE001 - fail safe, record a failed execution
            logger.exception("executor failed", extra={"proposal_id": proposal.id})
            result = ExecutionResult(
                status=ExecutionStatus.FAILED, error_code="EXECUTOR_ERROR"
            )
        execution_ref = result.execution_ref
        if execution_ref is not None:
            execution_ref = execution_ref[:128]
        error_code = result.error_code
        if error_code is not None:
            error_code = error_code[:64]
        execution = Execution(
            id=_new_id("EXEC"),
            proposal_id=proposal.id,
            status=result.status,
            execution_ref=execution_ref,
            error_code=error_code,
        )
        try:
            self.repo.create_execution(execution)
        except sqlite3.IntegrityError:
            existing = self.repo.get_execution_for_proposal(proposal.id)
            if existing is None:
                raise
            return existing
        event_type = (
            AuditEventType.EXECUTION_SUCCEEDED
            if result.status is ExecutionStatus.SUCCEEDED
            else AuditEventType.EXECUTION_FAILED
        )
        record_audit(
            self.repo,
            case_id=case.id,
            actor=identity.actor,
            role=identity.acting_role,
            event_type=event_type,
            outcome=result.status.value,
            reason_codes=[execution.error_code] if execution.error_code else [],
            payload={
                "proposal_id": proposal.id,
                "action": proposal.action.value,
                "execution_ref": execution.execution_ref,
                "error_code": execution.error_code,
            },
        )
        return execution


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _escalation_draft(case: Case, code: str, rationale: str) -> ProposalDraft:
    return ProposalDraft(
        payment_id=case.payment_id,
        action=Action.ESCALATE,
        rationale=rationale,
        reason_codes=[code],
    )
