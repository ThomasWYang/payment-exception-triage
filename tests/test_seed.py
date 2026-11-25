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

    by_id = {case.id: case for case in cases}
    assert len(cases) == 10
    assert created_events == 10
    assert all(case.status is CaseStatus.OPEN for case in cases)
    assert by_id["CASE-1009"].assignee == "user:carol"
    assert by_id["CASE-1010"].assignee == "user:bob"
