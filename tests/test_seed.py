from pathlib import Path

from payment_triage.db import connect
from payment_triage.models import AuditEventType, CaseStatus
from payment_triage.repository import Repository
from scripts.seed import seed


def test_seed_creates_ten_open_cases_with_expected_assignees(
    tmp_path: Path,
) -> None:
    database = tmp_path / "seed.db"
    seed(database)
    seed(database)
    conn = connect(database)
    try:
        repo = Repository(conn)
        cases = repo.list_cases(limit=100)
        created_events = repo.count_events(AuditEventType.CASE_CREATED)
    finally:
        conn.close()

    assert len(cases) == 10
    assert created_events == 10
    assert all(case.status is CaseStatus.OPEN for case in cases)
    expected_owners = {
        "CASE-1001": "user:carol",
        "CASE-1002": "user:alice",
        "CASE-1003": "user:dave",
        "CASE-1004": "user:bob",
        "CASE-1005": "user:alice",
        "CASE-1006": "user:dave",
        "CASE-1007": "user:dave",
        "CASE-1008": "user:alice",
        "CASE-1009": "user:carol",
        "CASE-1010": "user:bob",
    }
    assert {case.id: case.assignee for case in cases} == expected_owners
