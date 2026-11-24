from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from payment_triage.config import Settings, load_settings
from payment_triage.db import connect, init_schema
from payment_triage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        {
            "DATABASE_PATH": str(tmp_path / "test.db"),
            "POLICY_PATH": str(ROOT / "sample-data" / "policies.json"),
            "REFUND_CAP_CENTS": "1000000",
            "AUTO_RETRY_LIMIT_CENTS": "100000",
        }
    )


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn: sqlite3.Connection) -> Repository:
    return Repository(conn)
