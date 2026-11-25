"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CLOSED', 'BLOCKED'))
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency TEXT NOT NULL CHECK (currency = 'AUD'),
    status TEXT NOT NULL CHECK (status IN ('FAILED', 'SETTLED')),
    failure_code TEXT CHECK (
        failure_code IS NULL OR failure_code IN (
            'TEMPORARY_ERROR', 'INSUFFICIENT_FUNDS', 'ACCOUNT_CLOSED', 'LIMIT_EXCEEDED'
        )
    ),
    duplicate_of TEXT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL UNIQUE REFERENCES payments(id),
    exception_type TEXT NOT NULL CHECK (
        exception_type IN ('FAILED_PAYMENT', 'DUPLICATE_PAYMENT', 'CUSTOMER_DISPUTE')
    ),
    assignee TEXT NOT NULL,
    triage_requested_by TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('OPEN', 'PENDING_APPROVAL', 'EXECUTED', 'REJECTED', 'FAILED', 'ESCALATED')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES cases(id),
    action TEXT NOT NULL CHECK (
        action IN ('RETRY', 'HOLD', 'ESCALATE', 'REFUND_REQUEST')
    ),
    amount_cents INTEGER CHECK (amount_cents IS NULL OR amount_cents > 0),
    rationale TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    planner_version TEXT NOT NULL,
    policy_outcome TEXT NOT NULL CHECK (
        policy_outcome IN ('AUTO_APPROVED', 'PENDING_APPROVAL', 'REJECTED', 'ESCALATED')
    ),
    reason_codes_json TEXT NOT NULL,
    requires_approval INTEGER NOT NULL CHECK (requires_approval IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id),
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
    approver TEXT NOT NULL,
    rationale_redacted TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    execution_ref TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response_status INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    actor TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('AGENT', 'ANALYST', 'APPROVER')),
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_approvals_case ON approvals(case_id);
"""

AUDIT_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Additive migration for databases created before triage_requested_by.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
    if "triage_requested_by" not in columns:
        conn.execute("ALTER TABLE cases ADD COLUMN triage_requested_by TEXT")
    conn.executescript(AUDIT_TRIGGERS)
    conn.execute("PRAGMA user_version = 3")
