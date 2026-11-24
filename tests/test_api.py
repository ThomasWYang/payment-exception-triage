from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from payment_triage.api import create_app
from payment_triage.config import Settings
from payment_triage.db import connect
from payment_triage.models import Account, AccountStatus, Payment, PaymentStatus
from payment_triage.repository import Repository

ANALYST = {"X-API-Key": "analyst-key"}
AGENT = {"X-API-Key": "agent-key"}
APPROVER = {"X-API-Key": "approver-key"}
ALICE_APPROVER = {"X-API-Key": "approver-alice-key"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _seed_payment(
    settings: Settings,
    *,
    payment_id: str = "PAY-1",
    amount: int = 500000,
    status: PaymentStatus = PaymentStatus.FAILED,
    failure_code: str | None = "TEMPORARY_ERROR",
    account_status: AccountStatus = AccountStatus.ACTIVE,
) -> None:
    conn = connect(settings.database_path)
    try:
        repo = Repository(conn)
        repo.create_account(Account(id="ACC-1", status=account_status))
        repo.create_payment(
            Payment(
                id=payment_id,
                amount_cents=amount,
                status=status,
                failure_code=failure_code,
                account_id="ACC-1",
            )
        )
    finally:
        conn.close()


def _create_case(client: TestClient, payment_id: str = "PAY-1") -> str:
    response = client.post(
        "/v1/cases",
        json={"payment_id": payment_id, "exception_type": "FAILED_PAYMENT"},
        headers=ANALYST,
    )
    assert response.status_code == 201, response.text
    return response.json()["case"]["id"]


def test_health_ready_and_status(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "normal"
    assert "cases" in body
    assert body["auth_denials"] == 0


def test_auth_required(client: TestClient) -> None:
    response = client.get("/v1/cases")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["request_id"]
    assert response.headers["X-Request-ID"]
    assert client.get("/v1/cases", headers={"X-API-Key": "bad"}).status_code == 401
    assert client.get("/status").json()["auth_denials"] == 2


def test_role_matrix(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    assert client.post(f"/v1/cases/{case_id}/triage", headers=ANALYST).status_code == 403
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    assert client.get("/v1/cases", headers=AGENT).status_code == 403
    listed = client.get("/v1/cases", headers=APPROVER)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


def test_end_to_end_approval(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    approve = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "looks good"},
        headers={**APPROVER, "Idempotency-Key": "key-1"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["case"]["status"] == "EXECUTED"
    case = client.get(f"/v1/cases/{case_id}", headers=ANALYST)
    assert case.status_code == 200
    assert case.json()["execution"]["status"] == "SUCCEEDED"
    audit = client.get(f"/v1/cases/{case_id}/audit", headers=ANALYST)
    assert audit.status_code == 200
    event_types = [item["event_type"] for item in audit.json()["items"]]
    assert "APPROVED" in event_types
    assert "EXECUTION_SUCCEEDED" in event_types


def test_auto_retry_api(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=25000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "EXECUTED"
    assert client.get("/status").json()["cases"]["EXECUTED"] == 1


def test_validation_request_id_and_size(client: TestClient) -> None:
    response = client.post("/v1/cases", json={}, headers=ANALYST)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    provided = client.get("/healthz", headers={"X-Request-ID": "req-123"})
    assert provided.headers["X-Request-ID"] == "req-123"
    oversized = client.post("/v1/cases", content=b"x" * 70000, headers=ANALYST)
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_not_found(client: TestClient) -> None:
    response = client.get("/v1/cases/CASE-X", headers=ANALYST)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CASE_NOT_FOUND"


def test_self_approval_api(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    response = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "key-1"},
    )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "APPROVAL_DENIED"
    assert "SELF_APPROVAL_DENIED" in body["details"]["reason_codes"]


def test_idempotency_api(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    body = {"decision": "APPROVE", "rationale": "ok"}
    headers = {**APPROVER, "Idempotency-Key": "key-1"}
    first = client.post(f"/v1/cases/{case_id}/approve", json=body, headers=headers)
    second = client.post(f"/v1/cases/{case_id}/approve", json=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["case"]["status"] == "EXECUTED"
    audit = client.get(f"/v1/cases/{case_id}/audit", headers=ANALYST).json()
    assert "IDEMPOTENT_REPLAY" in [item["event_type"] for item in audit["items"]]
    missing = client.post(
        f"/v1/cases/{case_id}/approve", json=body, headers=APPROVER
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
