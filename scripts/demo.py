"""End-to-end demo of the payment exception triage workflow."""

from __future__ import annotations

import tempfile
from pathlib import Path

from payment_triage.config import Identity, Role, load_settings
from payment_triage.db import connect, init_schema
from payment_triage.executor import MockPaymentExecutor
from payment_triage.models import (
    Account,
    AccountStatus,
    ApproveRequest,
    Case,
    ExceptionType,
    Payment,
    PaymentStatus,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.repository import Repository
from payment_triage.workflow import Workflow, WorkflowError

ROOT = Path(__file__).resolve().parents[1]
AGENT = Identity("agent:triage-bot", frozenset({Role.AGENT}))
BOB = Identity("user:bob", frozenset({Role.ANALYST, Role.APPROVER}))
APPROVER = BOB.with_active_role(Role.APPROVER)
ALICE = Identity("user:alice", frozenset({Role.ANALYST, Role.APPROVER}))
ALICE_ANALYST = ALICE.with_active_role(Role.ANALYST)
ALICE_APPROVER = ALICE.with_active_role(Role.APPROVER)
CAROL_ANALYST = Identity("user:carol", frozenset({Role.ANALYST}))
DAVE_APPROVER = Identity("user:dave", frozenset({Role.APPROVER}))


def _payment(
    payment_id: str,
    amount: int,
    *,
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
        account_id=f"ACC-{payment_id}",
    )


def _seed(
    repo: Repository,
    case_id: str,
    payment: Payment,
    *,
    account_status: AccountStatus = AccountStatus.ACTIVE,
    exception_type: ExceptionType = ExceptionType.FAILED_PAYMENT,
    assignee: str = "user:alice",
) -> None:
    repo.create_account(Account(id=payment.account_id, status=account_status))
    repo.create_payment(payment)
    repo.create_case(
        Case(
            id=case_id,
            payment_id=payment.id,
            exception_type=exception_type,
            assignee=assignee,
        )
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        settings = load_settings(
            {
                "DATABASE_PATH": str(Path(tmp) / "demo.db"),
                "POLICY_PATH": str(ROOT / "sample-data" / "policies.json"),
            }
        )
        conn = connect(settings.database_path)
        init_schema(conn)
        repo = Repository(conn)
        workflow = Workflow(
            repo,
            settings,
            DeterministicPlanner(settings.refund_cap_cents),
            PolicyEngine(settings),
            MockPaymentExecutor("mock"),
        )
        rows: list[tuple[str, str, str]] = []
        try:
            _seed(repo, "CASE-D1", _payment("PAY-D1", 25000))
            result = workflow.triage("CASE-D1", AGENT)
            execution = result.execution.status.value if result.execution else "-"
            rows.append(("auto retry", result.case.status.value, f"execution={execution}"))

            _seed(repo, "CASE-D2", _payment("PAY-D2", 500000))
            workflow.triage("CASE-D2", AGENT)
            result = workflow.approve(
                "CASE-D2",
                APPROVER,
                ApproveRequest(decision="APPROVE", rationale="demo approval"),
                "demo-key-2",
            )
            rows.append(("high-value retry", result.case.status.value, "approved by user:bob"))

            _seed(
                repo,
                "CASE-D3",
                _payment(
                    "PAY-D3",
                    150000,
                    status=PaymentStatus.SETTLED,
                    failure_code=None,
                    duplicate_of="PAY-D3-ORIG",
                ),
                exception_type=ExceptionType.DUPLICATE_PAYMENT,
            )
            workflow.triage("CASE-D3", AGENT)
            result = workflow.approve(
                "CASE-D3",
                APPROVER,
                ApproveRequest(decision="APPROVE", rationale="duplicate confirmed"),
                "demo-key-3",
            )
            rows.append(("duplicate refund", result.case.status.value, "approved by user:bob"))

            _seed(
                repo,
                "CASE-D4",
                _payment("PAY-D4", 2_000_000, status=PaymentStatus.SETTLED, failure_code=None),
                exception_type=ExceptionType.CUSTOMER_DISPUTE,
            )
            result = workflow.triage("CASE-D4", AGENT)
            reasons = ",".join(result.policy.reason_codes) or "-"
            rows.append(("dispute over cap", result.case.status.value, reasons))

            _seed(
                repo,
                "CASE-D5",
                _payment("PAY-D5", 25000),
                account_status=AccountStatus.CLOSED,
            )
            result = workflow.triage("CASE-D5", AGENT)
            reasons = ",".join(result.policy.reason_codes) or "-"
            rows.append(("closed account", result.case.status.value, reasons))

            _seed(repo, "CASE-D6", _payment("PAY-D6", 500000))
            workflow.triage("CASE-D6", AGENT)
            try:
                workflow.approve(
                    "CASE-D6",
                    ALICE_APPROVER,
                    ApproveRequest(decision="APPROVE", rationale="self"),
                    "demo-key-6",
                )
                rows.append(("self-approval", "UNEXPECTED", "should have been denied"))
            except WorkflowError as exc:
                detail = ",".join(exc.reason_codes) or exc.code
                rows.append(("self-approval", "DENIED", detail))

            _seed(repo, "CASE-D7", _payment("PAY-D7", 500000))
            workflow.triage("CASE-D7", AGENT)
            request = ApproveRequest(decision="APPROVE", rationale="idempotent demo")
            first = workflow.approve("CASE-D7", APPROVER, request, "demo-key-7")
            second = workflow.approve("CASE-D7", APPROVER, request, "demo-key-7")
            view = workflow.get_case_view("CASE-D7")
            row = repo.conn.execute(
                "SELECT COUNT(*) AS n FROM executions WHERE proposal_id = ?",
                (view.proposal.id,),
            ).fetchone()
            outcome = (
                "IDEMPOTENT"
                if first.case.status == second.case.status and int(row["n"]) == 1
                else "FAILED"
            )
            rows.append(("duplicate approval", outcome, f"executions={row['n']}"))

            # Alice holds both roles; she can approve a case she did not
            # create or request triage for.
            _seed(
                repo,
                "CASE-D8",
                _payment("PAY-D8", 500000),
                assignee="user:carol",
            )
            workflow.triage("CASE-D8", CAROL_ANALYST)
            result = workflow.approve(
                "CASE-D8",
                ALICE_APPROVER,
                ApproveRequest(decision="APPROVE", rationale="independent review"),
                "demo-key-8",
            )
            rows.append(
                ("multi-role approval", result.case.status.value, "approved by user:alice")
            )

            # Dave is approver-only; he can approve a case he did not request.
            _seed(
                repo,
                "CASE-D10",
                _payment("PAY-D10", 500000),
                assignee="user:carol",
            )
            workflow.triage("CASE-D10", CAROL_ANALYST)
            result = workflow.approve(
                "CASE-D10",
                DAVE_APPROVER,
                ApproveRequest(decision="APPROVE", rationale="independent review"),
                "demo-key-10",
            )
            rows.append(
                ("single-role approval", result.case.status.value, "approved by user:dave")
            )

            # Alice requested triage on Carol's case; Alice's approver role
            # cannot then approve it, even though she is not the assignee.
            _seed(
                repo,
                "CASE-D9",
                _payment("PAY-D9", 500000),
                assignee="user:carol",
            )
            workflow.triage("CASE-D9", ALICE_ANALYST)
            try:
                workflow.approve(
                    "CASE-D9",
                    ALICE_APPROVER,
                    ApproveRequest(decision="APPROVE", rationale="mine"),
                    "demo-key-9",
                )
                rows.append(("triage requester", "UNEXPECTED", "should have been denied"))
            except WorkflowError as exc:
                detail = ",".join(exc.reason_codes) or exc.code
                rows.append(("triage requester", "DENIED", detail))
            result = workflow.approve(
                "CASE-D9",
                APPROVER,
                ApproveRequest(decision="APPROVE", rationale="independent review"),
                "demo-key-9b",
            )
            rows.append(
                ("independent approval", result.case.status.value, "approved by user:bob")
            )

            print(f"{'Scenario':<24} {'Outcome':<14} Detail")
            print("-" * 70)
            for name, outcome, detail in rows:
                print(f"{name:<24} {outcome:<14} {detail}")
            print("-" * 70)
            print(f"Case statuses: {workflow.status()['cases']}")
            audit_count = repo.conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events"
            ).fetchone()["n"]
            print(f"Audit events: {audit_count}")
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
