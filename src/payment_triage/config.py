"""Environment-driven configuration with safe synthetic local defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from payment_triage.models import Role


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(frozen=True)
class Identity:
    actor: str
    roles: frozenset[Role]
    active_role: Role | None = None

    def __post_init__(self) -> None:
        if not self.roles:
            raise ConfigError("Identity must have at least one role")
        object.__setattr__(self, "roles", frozenset(self.roles))
        if self.active_role is not None and self.active_role not in self.roles:
            raise ConfigError("active_role must be one of the identity's roles")

    @property
    def acting_role(self) -> Role:
        """The role used for this action, for audit and policy context."""
        if self.active_role is not None:
            return self.active_role
        return sorted(self.roles, key=lambda role: role.value)[0]

    def with_active_role(self, role: Role) -> "Identity":
        if role not in self.roles:
            raise ConfigError(f"Role {role.value} is not held by {self.actor}")
        return Identity(actor=self.actor, roles=self.roles, active_role=role)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    policy_path: Path
    refund_cap_cents: int
    auto_retry_limit_cents: int
    auto_approve_enabled: bool
    degraded_mode: bool
    executor_mode: str
    log_level: str
    max_request_bytes: int
    planner_max_tool_calls: int
    planner_timeout_seconds: float
    api_keys: Mapping[str, Identity]
    cors_origins: tuple[str, ...]
    app_version: str = "0.1.0"

    @property
    def effective_degraded(self) -> bool:
        """True when auto-approval must be disabled."""
        return self.degraded_mode or not self.auto_approve_enabled

    def identity_for(self, api_key: str | None) -> Identity | None:
        if api_key is None:
            return None
        return self.api_keys.get(api_key)


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _env_int(env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_float(
    env: Mapping[str, str], name: str, default: float, *, minimum: float | None = None
) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _parse_api_keys(env: Mapping[str, str]) -> dict[str, Identity]:
    configured = {
        "API_KEY_AGENT": (
            "agent-key",
            "agent:triage-bot",
            frozenset({Role.AGENT}),
        ),
        "API_KEY_ALICE": (
            "alice-key",
            "user:alice",
            frozenset({Role.ANALYST, Role.APPROVER}),
        ),
        "API_KEY_BOB": (
            "bob-key",
            "user:bob",
            frozenset({Role.ANALYST, Role.APPROVER}),
        ),
        "API_KEY_CAROL": (
            "carol-key",
            "user:carol",
            frozenset({Role.ANALYST}),
        ),
        "API_KEY_DAVE": (
            "dave-key",
            "user:dave",
            frozenset({Role.APPROVER}),
        ),
    }
    result: dict[str, Identity] = {}
    for env_name, (default_key, actor, roles) in configured.items():
        key = env.get(env_name, default_key).strip()
        if not key:
            raise ConfigError(f"{env_name} must be non-empty")
        if key in result:
            raise ConfigError(f"Duplicate API key configured via {env_name}")
        result[key] = Identity(actor=actor, roles=roles)
    return result


def _parse_cors_origins(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    origins = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    if not origins:
        raise ConfigError("CORS_ORIGINS must contain at least one origin")
    return origins


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    raw = os.environ if env is None else env
    executor_mode = raw.get("EXECUTOR_MODE", "mock").strip().lower()
    if executor_mode not in {"mock", "fail"}:
        raise ConfigError("EXECUTOR_MODE must be 'mock' or 'fail'")
    log_level = raw.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"Invalid LOG_LEVEL: {log_level}")

    return Settings(
        database_path=Path(raw.get("DATABASE_PATH", "var/payment_triage.db")),
        policy_path=Path(raw.get("POLICY_PATH", "sample-data/policies.json")),
        refund_cap_cents=_env_int(raw, "REFUND_CAP_CENTS", 1_000_000, minimum=1),
        auto_retry_limit_cents=_env_int(raw, "AUTO_RETRY_LIMIT_CENTS", 100_000, minimum=0),
        auto_approve_enabled=_env_bool(raw, "AUTO_APPROVE_ENABLED", True),
        degraded_mode=_env_bool(raw, "DEGRADED_MODE", False),
        executor_mode=executor_mode,
        log_level=log_level,
        max_request_bytes=_env_int(raw, "MAX_REQUEST_BYTES", 65_536, minimum=1024),
        planner_max_tool_calls=_env_int(raw, "PLANNER_MAX_TOOL_CALLS", 8, minimum=1),
        planner_timeout_seconds=_env_float(raw, "PLANNER_TIMEOUT_SECONDS", 5.0, minimum=0.1),
        api_keys=_parse_api_keys(raw),
        cors_origins=_parse_cors_origins(raw),
        app_version=raw.get("APP_VERSION", "0.1.0"),
    )
