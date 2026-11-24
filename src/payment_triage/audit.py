"""Audit helpers: canonical payload hashing and event recording."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from payment_triage.models import AuditEvent, AuditEventType, Role
from payment_triage.repository import Repository


def payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_audit(
    repo: Repository,
    *,
    case_id: str | None,
    actor: str,
    role: Role,
    event_type: AuditEventType,
    outcome: str,
    reason_codes: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    event = AuditEvent(
        case_id=case_id,
        actor=actor,
        actor_role=role,
        event_type=event_type,
        outcome=outcome,
        reason_codes=reason_codes or [],
        payload_hash=payload_hash(payload or {}),
    )
    return repo.append_audit(event)
