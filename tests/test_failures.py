import time

from payment_triage.config import Identity, load_settings
from payment_triage.executor import MockPaymentExecutor
from payment_triage.models import (
    Account,
    AccountStatus,
    Action,
    Case,
    CaseStatus,
    ExceptionType,
    Payment,
    PaymentStatus,
    ProposalDraft,
    Role,
)
from payment_triage.planner import DeterministicPlanner
from payment_triage.policy import PolicyEngine
from payment_triage.workflow import Workflow

AGENT = Identity("agent:triage-bot", Role.AGENT)


def _seed(repo) -> Payment:
    repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
    payment = Payment(
        id="PAY-1",
        amount_cents=25000,
        status=PaymentStatus.FAILED,
        failure_code="TEMPORARY_ERROR",
        account_id="ACC-1",
    )
    repo.create_payment(payment)
    repo.create_case(
        Case(
            id="CASE-1",
            payment_id="PAY-1",
            exception_type=ExceptionType.FAILED_PAYMENT,
            assignee="user:alice",
        )
    )
    return payment


def _workflow(repo, settings, *, planner=None, executor=None) -> Workflow:
    return Workflow(
        repo=repo,
        settings=settings,
        planner=planner or DeterministicPlanner(settings.refund_cap_cents),
        policy=PolicyEngine(settings),
        executor=executor or MockPaymentExecutor("mock"),
    )


class ExplodingPlanner:
    version = "exploding-v1"

    def plan(self, case, gateway):
        raise RuntimeError("boom")


class SlowPlanner:
    version = "slow-v1"

    def plan(self, case, gateway):
        time.sleep(0.2)
        return ProposalDraft(
            payment_id=case.payment_id, action=Action.ESCALATE, rationale="slow"
        )


class ExplodingExecutor:
    def execute(self, proposal, payment):
        raise RuntimeError("boom")


def test_planner_exception_escalates(repo, settings) -> None:
    _seed(repo)
    wf = _workflow(repo, settings, planner=ExplodingPlanner())
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.ESCALATED
    assert "PLANNER_ERROR" in result.policy.reason_codes
    assert result.execution is None


def test_planner_timeout_escalates(repo, settings) -> None:
    slow = load_settings(
        {
            "PLANNER_TIMEOUT_SECONDS": "0.1",
            "AUTO_RETRY_LIMIT_CENTS": "100000",
            "REFUND_CAP_CENTS": "1000000",
        }
    )
    _seed(repo)
    wf = _workflow(repo, slow, planner=SlowPlanner())
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.ESCALATED
    assert "PLANNER_TIMEOUT" in result.policy.reason_codes


def test_executor_exception_marks_failed(repo, settings) -> None:
    _seed(repo)
    wf = _workflow(repo, settings, executor=ExplodingExecutor())
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.FAILED
    assert result.execution is not None
    assert result.execution.error_code == "EXECUTOR_ERROR"


def test_executor_failure_mode(repo, settings) -> None:
    _seed(repo)
    wf = _workflow(repo, settings, executor=MockPaymentExecutor("fail"))
    result = wf.triage("CASE-1", AGENT)
    assert result.case.status is CaseStatus.FAILED
    assert result.execution is not None
    assert result.execution.error_code == "MOCK_EXECUTOR_FAILURE"
