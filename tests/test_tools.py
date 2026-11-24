import pytest

from payment_triage.models import Account, AccountStatus, Payment, PaymentStatus
from payment_triage.tools import (
    AccountLookupArgs,
    AccountLookupResult,
    PaymentLookupArgs,
    PaymentLookupResult,
    PolicySearchResult,
    ToolError,
    ToolGateway,
)


def _seed(repo) -> None:
    repo.create_account(Account(id="ACC-1", status=AccountStatus.ACTIVE))
    repo.create_payment(
        Payment(
            id="PAY-1",
            amount_cents=1000,
            status=PaymentStatus.FAILED,
            failure_code="TEMPORARY_ERROR",
            account_id="ACC-1",
        )
    )


def test_payment_and_account_lookup(repo, settings) -> None:
    _seed(repo)
    gateway = ToolGateway(repo, settings.policy_path)
    payment = gateway.call("payment.lookup", {"payment_id": "PAY-1"})
    assert isinstance(payment, PaymentLookupResult)
    assert payment.payment.id == "PAY-1"
    account = gateway.call("account.lookup", {"account_id": "ACC-1"})
    assert isinstance(account, AccountLookupResult)
    assert account.account.status is AccountStatus.ACTIVE
    assert len(gateway.calls) == 2
    assert gateway.calls[0].outcome == "OK"


def test_unknown_tool_and_invalid_args(repo, settings) -> None:
    _seed(repo)
    gateway = ToolGateway(repo, settings.policy_path)
    with pytest.raises(ToolError) as exc:
        gateway.call("refund.execute", {})
    assert exc.value.code == "TOOL_NOT_ALLOWED"
    with pytest.raises(ToolError) as exc:
        gateway.call("payment.lookup", {"payment_id": ""})
    assert exc.value.code == "INVALID_TOOL_ARGS"


def test_not_found(repo, settings) -> None:
    gateway = ToolGateway(repo, settings.policy_path)
    with pytest.raises(ToolError) as exc:
        gateway.call("payment.lookup", {"payment_id": "PAY-X"})
    assert exc.value.code == "NOT_FOUND"


def test_policy_search(repo, settings) -> None:
    gateway = ToolGateway(repo, settings.policy_path)
    result = gateway.call("policy.search", {"query": "temporary_error retry", "limit": 2})
    assert isinstance(result, PolicySearchResult)
    assert result.matches
    assert result.matches[0].id == "POL-RETRY"
    empty = gateway.call("policy.search", {"query": "zzzz", "limit": 2})
    assert isinstance(empty, PolicySearchResult)
    assert empty.matches == []


def test_tool_call_limit(repo, settings) -> None:
    _seed(repo)
    gateway = ToolGateway(repo, settings.policy_path, max_calls=1)
    gateway.call("payment.lookup", {"payment_id": "PAY-1"})
    with pytest.raises(ToolError) as exc:
        gateway.call("account.lookup", {"account_id": "ACC-1"})
    assert exc.value.code == "TOOL_CALL_LIMIT"


def test_invalid_attempts_count_toward_limit(repo, settings) -> None:
    gateway = ToolGateway(repo, settings.policy_path, max_calls=1)
    with pytest.raises(ToolError) as exc:
        gateway.call("payment.lookup", {"payment_id": ""})
    assert exc.value.code == "INVALID_TOOL_ARGS"
    with pytest.raises(ToolError) as exc:
        gateway.call("payment.lookup", {"payment_id": "PAY-1"})
    assert exc.value.code == "TOOL_CALL_LIMIT"


def test_wrong_model_type_and_constructed_args_rejected(repo, settings) -> None:
    gateway = ToolGateway(repo, settings.policy_path)
    with pytest.raises(ToolError) as exc:
        gateway.call("payment.lookup", AccountLookupArgs(account_id="ACC-1"))
    assert exc.value.code == "INVALID_TOOL_ARGS"
    with pytest.raises(ToolError) as exc:
        gateway.call(
            "payment.lookup", PaymentLookupArgs.model_construct(payment_id="")
        )
    assert exc.value.code == "INVALID_TOOL_ARGS"


def test_gateway_does_not_expose_repository(repo, settings) -> None:
    gateway = ToolGateway(repo, settings.policy_path)
    assert not hasattr(gateway, "repo")
