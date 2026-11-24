"""Golden-case evaluation gate for the triage workflow."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from payment_triage.config import Identity, Role, load_settings
from payment_triage.db import connect, init_schema
from payment_triage.executor import MockPaymentExecutor
from payment_triage.models import Account, ApproveRequest, Case, ExceptionType, Payment
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.repository import Repository
from payment_triage.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "sample-data" / "golden_cases.jsonl"
AGENT = Identity("agent:eval", Role.AGENT)
APPROVER = Identity("user:bob", Role.APPROVER)


def _load_cases() -> list[dict]:
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _check_idempotency(
    workflow: Workflow, repo: Repository, case_id: str | None
) -> bool:
    if case_id is None:
        return False
    try:
        view = workflow.get_case_view(case_id)
        if view.proposal is None:
            return False
        request = ApproveRequest(decision="APPROVE", rationale="eval idempotency check")
        first = workflow.approve(case_id, APPROVER, request, "eval-idempotency-key")
        second = workflow.approve(case_id, APPROVER, request, "eval-idempotency-key")
        row = repo.conn.execute(
            "SELECT COUNT(*) AS n FROM executions WHERE proposal_id = ?",
            (view.proposal.id,),
        ).fetchone()
        audit = [event.event_type.value for event in workflow.get_audit(case_id)]
        return (
            first.case.status.value == "EXECUTED"
            and second.case.status.value == "EXECUTED"
            and int(row["n"]) == 1
            and "IDEMPOTENT_REPLAY" in audit
        )
    except Exception as exc:  # noqa: BLE001 - report as a failed gate
        print(f"  idempotency check error: {exc}")
        return False


def run_eval() -> int:
    cases = _load_cases()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "eval.db"
        settings = load_settings(
            {
                "DATABASE_PATH": str(db_path),
                "POLICY_PATH": str(ROOT / "sample-data" / "policies.json"),
                "REFUND_CAP_CENTS": "1000000",
                "AUTO_RETRY_LIMIT_CENTS": "100000",
            }
        )
        conn = connect(db_path)
        init_schema(conn)
        repo = Repository(conn)
        workflow = Workflow(
            repo,
            settings,
            DeterministicPlanner(settings.refund_cap_cents),
            PolicyEngine(settings),
            MockPaymentExecutor("mock"),
        )
        action_ok = policy_ok = status_ok = reasons_ok = 0
        forbidden = 0
        auto = pending = rejected = escalated = 0
        mismatches: list[str] = []
        latencies: list[float] = []
        pending_case_id: str | None = None
        try:
            for case in cases:
                case_id = f"{case['id']}-CASE"
                payment_raw = case["payment"]
                account_id = payment_raw["account_id"]
                repo.create_account(
                    Account(id=account_id, status=case["account_status"])
                )
                repo.create_payment(Payment.model_validate(payment_raw))
                repo.create_case(
                    Case(
                        id=case_id,
                        payment_id=payment_raw["id"],
                        exception_type=ExceptionType(case["exception_type"]),
                        assignee="user:alice",
                    )
                )
                started = time.monotonic()
                result = workflow.triage(case_id, AGENT)
                latencies.append((time.monotonic() - started) * 1000)

                if result.proposal.action.value == case["expected_action"]:
                    action_ok += 1
                else:
                    mismatches.append(
                        f"{case['id']}: action {result.proposal.action.value} "
                        f"!= {case['expected_action']}"
                    )
                if result.policy.outcome.value == case["expected_policy_outcome"]:
                    policy_ok += 1
                else:
                    mismatches.append(
                        f"{case['id']}: policy {result.policy.outcome.value} "
                        f"!= {case['expected_policy_outcome']}"
                    )
                if result.case.status.value == case["expected_status"]:
                    status_ok += 1
                else:
                    mismatches.append(
                        f"{case['id']}: status {result.case.status.value} "
                        f"!= {case['expected_status']}"
                    )
                missing = set(case["expected_reason_codes"]) - set(
                    result.policy.reason_codes
                )
                if not missing:
                    reasons_ok += 1
                else:
                    mismatches.append(
                        f"{case['id']}: missing reason codes {sorted(missing)}"
                    )

                if (
                    case["expected_policy_outcome"]
                    in {"REJECTED", "ESCALATED", "PENDING_APPROVAL"}
                    and result.execution is not None
                ):
                    forbidden += 1
                    mismatches.append(f"{case['id']}: forbidden execution")
                if case["expected_status"] == "EXECUTED" and (
                    result.execution is None
                    or result.execution.status.value != "SUCCEEDED"
                ):
                    mismatches.append(f"{case['id']}: expected a successful execution")

                if case["expected_policy_outcome"] == "AUTO_APPROVED":
                    auto += 1
                elif case["expected_policy_outcome"] == "PENDING_APPROVAL":
                    pending += 1
                    if pending_case_id is None:
                        pending_case_id = case_id
                elif case["expected_policy_outcome"] == "REJECTED":
                    rejected += 1
                elif case["expected_policy_outcome"] == "ESCALATED":
                    escalated += 1

            idempotency_ok = _check_idempotency(workflow, repo, pending_case_id)
        finally:
            conn.close()

        latencies.sort()
        total = len(cases)
        p95 = (
            latencies[min(total - 1, int(total * 0.95))]
            if latencies
            else 0.0
        )
        print(f"Golden cases: {total}")
        print(
            f"  auto-approved: {auto}  pending: {pending}  "
            f"rejected: {rejected}  escalated: {escalated}"
        )
        print(f"  action accuracy: {action_ok}/{total}")
        print(f"  policy accuracy: {policy_ok}/{total}")
        print(f"  status accuracy: {status_ok}/{total}")
        print(f"  reason-code accuracy: {reasons_ok}/{total}")
        print(f"  forbidden executions: {forbidden}")
        print(f"  idempotency replay: {'PASS' if idempotency_ok else 'FAIL'}")
        print(f"  p95 triage latency: {p95:.1f} ms")
        if mismatches:
            print("Mismatches:")
            for mismatch in mismatches:
                print(f"  - {mismatch}")
        ok = not mismatches and forbidden == 0 and idempotency_ok
        print(f"RESULT: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_eval())
