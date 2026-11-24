import pytest

from payment_triage.config import ConfigError, load_settings
from payment_triage.models import Role


def test_defaults_are_safe_and_synthetic() -> None:
    settings = load_settings({})
    assert settings.refund_cap_cents == 1_000_000
    assert settings.auto_retry_limit_cents == 100_000
    assert settings.executor_mode == "mock"
    assert settings.effective_degraded is False
    assert len(settings.api_keys) == 4
    assert settings.identity_for("agent-key").role is Role.AGENT
    assert settings.identity_for("approver-alice-key").actor == "user:alice"
    assert settings.identity_for("unknown") is None
    assert settings.identity_for(None) is None


def test_env_overrides() -> None:
    settings = load_settings(
        {
            "REFUND_CAP_CENTS": "5000",
            "AUTO_RETRY_LIMIT_CENTS": "1000",
            "AUTO_APPROVE_ENABLED": "false",
            "DEGRADED_MODE": "true",
            "EXECUTOR_MODE": "fail",
            "LOG_LEVEL": "debug",
            "MAX_REQUEST_BYTES": "2048",
            "PLANNER_MAX_TOOL_CALLS": "3",
            "PLANNER_TIMEOUT_SECONDS": "1.5",
        }
    )
    assert settings.refund_cap_cents == 5000
    assert settings.auto_retry_limit_cents == 1000
    assert settings.auto_approve_enabled is False
    assert settings.degraded_mode is True
    assert settings.effective_degraded is True
    assert settings.executor_mode == "fail"
    assert settings.log_level == "DEBUG"
    assert settings.max_request_bytes == 2048
    assert settings.planner_max_tool_calls == 3
    assert settings.planner_timeout_seconds == 1.5


@pytest.mark.parametrize(
    "env",
    [
        {"REFUND_CAP_CENTS": "0"},
        {"REFUND_CAP_CENTS": "-1"},
        {"AUTO_RETRY_LIMIT_CENTS": "-1"},
        {"EXECUTOR_MODE": "bogus"},
        {"LOG_LEVEL": "LOUD"},
        {"MAX_REQUEST_BYTES": "10"},
        {"PLANNER_TIMEOUT_SECONDS": "0"},
        {"AUTO_APPROVE_ENABLED": "maybe"},
        {"API_KEY_AGENT": ""},
        {"API_KEY_AGENT": "analyst-key"},
    ],
)
def test_invalid_config_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_settings(env)
