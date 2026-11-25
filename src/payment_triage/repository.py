"""SQLite repository with parameterized queries and explicit transactions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from payment_triage.models import (
    Account,
    Action,
    Approval,
    AuditEvent,
    AuditEventType,
    Case,
    CaseStatus,
    EvidenceRef,
    Execution,
    Payment,
    Proposal,
    utcnow_iso,
)


class Repository:
    """Small data-access layer over a single SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Begin an immediate transaction, committing or rolling back."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        """Begin a deferred read transaction for a consistent snapshot."""
        self.conn.execute("BEGIN DEFERRED")
        try:
            yield
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # Accounts -----------------------------------------------------------------

    def create_account(self, account: Account) -> None:
        self.conn.execute(
            "INSERT INTO accounts (id, status) VALUES (?, ?)",
            (account.id, account.status.value),
        )

    def get_account(self, account_id: str) -> Account | None:
        row = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return _account_from_row(row) if row else None

    # Payments -----------------------------------------------------------------

    def create_payment(self, payment: Payment) -> None:
        self.conn.execute(
            """INSERT INTO payments
               (id, amount_cents, currency, status, failure_code, duplicate_of,
                account_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payment.id,
                payment.amount_cents,
                payment.currency,
                payment.status.value,
                payment.failure_code.value if payment.failure_code else None,
                payment.duplicate_of,
                payment.account_id,
                payment.created_at,
            ),
        )

    def get_payment(self, payment_id: str) -> Payment | None:
        row = self.conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        return _payment_from_row(row) if row else None

    # Cases --------------------------------------------------------------------

    def create_case(self, case: Case) -> None:
        self.conn.execute(
            """INSERT INTO cases
               (id, payment_id, exception_type, assignee, triage_requested_by,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case.id,
                case.payment_id,
                case.exception_type.value,
                case.assignee,
                case.triage_requested_by,
                case.status.value,
                case.created_at,
                case.updated_at,
            ),
        )

    def get_case(self, case_id: str) -> Case | None:
        row = self.conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return _case_from_row(row) if row else None

    def get_case_by_payment(self, payment_id: str) -> Case | None:
        row = self.conn.execute(
            "SELECT * FROM cases WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        return _case_from_row(row) if row else None

    def list_cases(
        self, status: CaseStatus | None = None, limit: int = 50, offset: int = 0
    ) -> list[Case]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM cases ORDER BY rowid LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM cases WHERE status = ? ORDER BY rowid LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            ).fetchall()
        return [_case_from_row(row) for row in rows]

    def set_triage_requested_by(self, case_id: str, actor: str) -> None:
        self.conn.execute(
            "UPDATE cases SET triage_requested_by = ?, updated_at = ? WHERE id = ?",
            (actor, utcnow_iso(), case_id),
        )

    def update_case_status(self, case_id: str, expected: CaseStatus, new: CaseStatus) -> bool:
        cursor = self.conn.execute(
            "UPDATE cases SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new.value, utcnow_iso(), case_id, expected.value),
        )
        return cursor.rowcount == 1

    # Proposals ----------------------------------------------------------------

    def create_proposal(self, proposal: Proposal) -> None:
        self.conn.execute(
            """INSERT INTO proposals
               (id, case_id, action, amount_cents, rationale, evidence_json, planner_version,
                policy_outcome, reason_codes_json, requires_approval, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                proposal.id,
                proposal.case_id,
                proposal.action.value,
                proposal.amount_cents,
                proposal.rationale,
                json.dumps(
                    [item.model_dump() for item in proposal.evidence],
                    separators=(",", ":"),
                ),
                proposal.planner_version,
                proposal.policy_outcome.value,
                json.dumps(proposal.reason_codes, separators=(",", ":")),
                int(proposal.requires_approval),
                proposal.created_at,
            ),
        )

    def get_proposal_for_case(self, case_id: str) -> Proposal | None:
        row = self.conn.execute("SELECT * FROM proposals WHERE case_id = ?", (case_id,)).fetchone()
        return _proposal_from_row(row) if row else None

    # Approvals ----------------------------------------------------------------

    def create_approval(self, approval: Approval) -> None:
        self.conn.execute(
            """INSERT INTO approvals
               (id, case_id, proposal_id, decision, approver, rationale_redacted, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                approval.id,
                approval.case_id,
                approval.proposal_id,
                approval.decision.value,
                approval.approver,
                approval.rationale_redacted,
                approval.created_at,
            ),
        )

    def get_approval_for_proposal(self, proposal_id: str) -> Approval | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return _approval_from_row(row) if row else None

    # Executions ---------------------------------------------------------------

    def create_execution(self, execution: Execution) -> None:
        self.conn.execute(
            """INSERT INTO executions
               (id, proposal_id, status, execution_ref, error_code, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                execution.id,
                execution.proposal_id,
                execution.status.value,
                execution.execution_ref,
                execution.error_code,
                execution.created_at,
            ),
        )

    def get_execution_for_proposal(self, proposal_id: str) -> Execution | None:
        row = self.conn.execute(
            "SELECT * FROM executions WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return _execution_from_row(row) if row else None

    # Idempotency --------------------------------------------------------------

    def get_idempotency(self, key: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def save_idempotency(
        self, key: str, request_hash: str, response_status: int, response_json: str
    ) -> None:
        self.conn.execute(
            """INSERT INTO idempotency_keys
               (key, request_hash, response_status, response_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (key, request_hash, response_status, response_json, utcnow_iso()),
        )

    # Audit --------------------------------------------------------------------

    def append_audit(self, event: AuditEvent) -> int:
        cursor = self.conn.execute(
            """INSERT INTO audit_events
               (case_id, actor, actor_role, event_type, outcome, reason_codes_json,
                payload_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.case_id,
                event.actor,
                event.actor_role.value,
                event.event_type.value,
                event.outcome,
                json.dumps(event.reason_codes, separators=(",", ":")),
                event.payload_hash,
                event.created_at,
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_audit(
        self, case_id: str, limit: int = 500, offset: int = 0
    ) -> list[AuditEvent]:
        rows = self.conn.execute(
            """SELECT * FROM audit_events
               WHERE case_id = ? ORDER BY id LIMIT ? OFFSET ?""",
            (case_id, limit, offset),
        ).fetchall()
        return [_audit_from_row(row) for row in rows]

    # Status -------------------------------------------------------------------

    def status_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in CaseStatus}
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
        ).fetchall()
        for row in rows:
            counts[row["status"]] = int(row["n"])
        return counts

    def count_events(self, event_type: AuditEventType) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE event_type = ?",
            (event_type.value,),
        ).fetchone()
        return int(row["n"])


def _account_from_row(row: sqlite3.Row) -> Account:
    return Account(id=row["id"], status=row["status"])


def _payment_from_row(row: sqlite3.Row) -> Payment:
    return Payment(
        id=row["id"],
        amount_cents=row["amount_cents"],
        currency=row["currency"],
        status=row["status"],
        failure_code=row["failure_code"],
        duplicate_of=row["duplicate_of"],
        account_id=row["account_id"],
        created_at=row["created_at"],
    )


def _case_from_row(row: sqlite3.Row) -> Case:
    return Case(
        id=row["id"],
        payment_id=row["payment_id"],
        exception_type=row["exception_type"],
        assignee=row["assignee"],
        triage_requested_by=row["triage_requested_by"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _proposal_from_row(row: sqlite3.Row) -> Proposal:
    evidence = [EvidenceRef.model_validate(item) for item in json.loads(row["evidence_json"])]
    return Proposal(
        id=row["id"],
        case_id=row["case_id"],
        action=Action(row["action"]),
        amount_cents=row["amount_cents"],
        rationale=row["rationale"],
        evidence=evidence,
        planner_version=row["planner_version"],
        policy_outcome=row["policy_outcome"],
        reason_codes=json.loads(row["reason_codes_json"]),
        requires_approval=bool(row["requires_approval"]),
        created_at=row["created_at"],
    )


def _approval_from_row(row: sqlite3.Row) -> Approval:
    return Approval(
        id=row["id"],
        case_id=row["case_id"],
        proposal_id=row["proposal_id"],
        decision=row["decision"],
        approver=row["approver"],
        rationale_redacted=row["rationale_redacted"],
        created_at=row["created_at"],
    )


def _execution_from_row(row: sqlite3.Row) -> Execution:
    return Execution(
        id=row["id"],
        proposal_id=row["proposal_id"],
        status=row["status"],
        execution_ref=row["execution_ref"],
        error_code=row["error_code"],
        created_at=row["created_at"],
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        case_id=row["case_id"],
        actor=row["actor"],
        actor_role=row["actor_role"],
        event_type=row["event_type"],
        outcome=row["outcome"],
        reason_codes=json.loads(row["reason_codes_json"]),
        payload_hash=row["payload_hash"],
        created_at=row["created_at"],
    )
