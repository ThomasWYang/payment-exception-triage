from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from payment_triage.api import create_app
from payment_triage.config import Settings, load_settings
from payment_triage.db import connect
from payment_triage.models import Account, AccountStatus, Payment, PaymentStatus
from payment_triage.repository import Repository

ALICE = {"X-API-Key": "alice-key"}
ALICE_ANALYST = {**ALICE, "X-Acting-Role": "ANALYST"}
ALICE_APPROVER = {**ALICE, "X-Acting-Role": "APPROVER"}
BOB = {"X-API-Key": "bob-key"}
BOB_ANALYST = {**BOB, "X-Acting-Role": "ANALYST"}
BOB_APPROVER = {**BOB, "X-Acting-Role": "APPROVER"}
CAROL = {"X-API-Key": "carol-key"}
CAROL_ANALYST = {**CAROL, "X-Acting-Role": "ANALYST"}
DAVE = {"X-API-Key": "dave-key"}
DAVE_APPROVER = {**DAVE, "X-Acting-Role": "APPROVER"}
AGENT = {"X-API-Key": "agent-key"}
UI_DIST = Path(__file__).resolve().parents[1] / "ui" / "dist"


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
        account_id = f"ACC-{payment_id}"
        repo.create_account(Account(id=account_id, status=account_status))
        repo.create_payment(
            Payment(
                id=payment_id,
                amount_cents=amount,
                status=status,
                failure_code=failure_code,
                account_id=account_id,
            )
        )
    finally:
        conn.close()


def _create_case(
    client: TestClient,
    payment_id: str = "PAY-1",
    headers: dict[str, str] | None = None,
) -> str:
    response = client.post(
        "/v1/cases",
        json={"payment_id": payment_id, "exception_type": "FAILED_PAYMENT"},
        headers=headers or ALICE_ANALYST,
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
    assert body["auto_retry_limit_cents"] == 100000
    assert body["refund_cap_cents"] == 1000000
    assert body["auto_approve_enabled"] is True
    assert body["degraded_mode"] is False


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
    case_id = _create_case(client, headers=CAROL_ANALYST)
    # Dave is approver-only: he cannot request triage, with or without an
    # explicit active role.
    assert (
        client.post(f"/v1/cases/{case_id}/triage", headers=DAVE_APPROVER).status_code
        == 403
    )
    dave_default = client.post(f"/v1/cases/{case_id}/triage", headers=DAVE)
    assert dave_default.status_code == 403
    assert dave_default.json()["error"]["code"] == "FORBIDDEN"
    # Carol is analyst-only: she can request triage.
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=CAROL_ANALYST)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    assert triage.json()["case"]["triage_requested_by"] == "user:carol"
    # Carol is analyst-only: she cannot approve.
    denied = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "not allowed"},
        headers={**CAROL, "Idempotency-Key": "role-matrix-1"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    # Dave is approver-only: he can approve Carol's case.
    approved = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "independent"},
        headers={**DAVE_APPROVER, "Idempotency-Key": "role-matrix-2"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"
    # The agent service cannot read case data; the humans can.
    assert client.get("/v1/cases", headers=AGENT).status_code == 403
    assert client.get(f"/v1/cases/{case_id}", headers=AGENT).status_code == 403
    assert (
        client.get(f"/v1/cases/{case_id}/audit", headers=AGENT).status_code == 403
    )
    agent_role = client.get(
        f"/v1/cases/{case_id}", headers={**AGENT, "X-Acting-Role": "AGENT"}
    )
    assert agent_role.status_code == 403
    assert agent_role.json()["error"]["code"] == "ACTIVE_ROLE_MISMATCH"
    assert client.get("/v1/cases", headers=ALICE_ANALYST).status_code == 200
    assert client.get("/v1/cases", headers=BOB_APPROVER).status_code == 200
    assert client.get("/v1/cases", headers=CAROL_ANALYST).status_code == 200
    assert client.get("/v1/cases", headers=DAVE_APPROVER).status_code == 200


def test_active_role_selection(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=25000)
    payload = {"payment_id": "PAY-1", "exception_type": "FAILED_PAYMENT"}
    created = client.post("/v1/cases", json=payload, headers=ALICE_ANALYST)
    assert created.status_code == 201
    case_id = created.json()["case"]["id"]
    mismatch = client.post("/v1/cases", json=payload, headers=ALICE_APPROVER)
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "ACTIVE_ROLE_MISMATCH"
    triage_mismatch = client.post(
        f"/v1/cases/{case_id}/triage", json={}, headers=ALICE_APPROVER
    )
    assert triage_mismatch.status_code == 403
    assert triage_mismatch.json()["error"]["code"] == "ACTIVE_ROLE_MISMATCH"
    approve_mismatch = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "wrong role"},
        headers={**ALICE_ANALYST, "Idempotency-Key": "active-role-1"},
    )
    assert approve_mismatch.status_code == 403
    assert approve_mismatch.json()["error"]["code"] == "ACTIVE_ROLE_MISMATCH"
    normalized = client.get(
        "/v1/cases", headers={**ALICE, "X-Acting-Role": " analyst "}
    )
    assert normalized.status_code == 200
    not_held = client.get("/v1/cases", headers={**ALICE, "X-Acting-Role": "AGENT"})
    assert not_held.status_code == 403
    assert not_held.json()["error"]["code"] == "FORBIDDEN"
    invalid = client.get(
        "/v1/cases", headers={**ALICE, "X-Acting-Role": "SUPERUSER"}
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_ACTING_ROLE"


def test_default_active_role(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    # No X-Acting-Role: the service selects the first allowed role the
    # identity holds (ANALYST on read/triage endpoints, APPROVER on the
    # approve endpoint).
    case_id = _create_case(client, headers=ALICE)
    triage = client.post(f"/v1/cases/{case_id}/triage", json={}, headers=ALICE)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["triage_requested_by"] == "user:alice"
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    assert client.get("/v1/cases", headers=BOB).status_code == 200
    approved = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "default role"},
        headers={**BOB, "Idempotency-Key": "default-role-1"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"


def test_agent_can_request_triage(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", json={}, headers=AGENT)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    assert triage.json()["case"]["triage_requested_by"] == "agent:triage-bot"


def test_multi_role_boundary(client: TestClient, settings: Settings) -> None:
    # Bob (analyst + approver) owns and triages his case; Alice approves it.
    _seed_payment(settings, payment_id="PAY-A", amount=500000)
    bob_case = _create_case(client, payment_id="PAY-A", headers=BOB_ANALYST)
    triage = client.post(f"/v1/cases/{bob_case}/triage", headers=BOB_ANALYST)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["triage_requested_by"] == "user:bob"
    denied = client.post(
        f"/v1/cases/{bob_case}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**BOB_APPROVER, "Idempotency-Key": "cross-a-denied"},
    )
    assert denied.status_code == 403
    reasons = denied.json()["error"]["details"]["reason_codes"]
    assert "SELF_APPROVAL_DENIED" in reasons
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in reasons
    approved = client.post(
        f"/v1/cases/{bob_case}/approve",
        json={"decision": "APPROVE", "rationale": "independent review"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "cross-a"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"

    # Alice owns and triages her case; she cannot approve it, but Bob can.
    _seed_payment(settings, payment_id="PAY-B", amount=500000)
    alice_case = _create_case(client, payment_id="PAY-B", headers=ALICE_ANALYST)
    triage = client.post(f"/v1/cases/{alice_case}/triage", headers=ALICE_ANALYST)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["triage_requested_by"] == "user:alice"
    denied = client.post(
        f"/v1/cases/{alice_case}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "cross-b"},
    )
    assert denied.status_code == 403
    reasons = denied.json()["error"]["details"]["reason_codes"]
    assert "SELF_APPROVAL_DENIED" in reasons
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in reasons
    approved = client.post(
        f"/v1/cases/{alice_case}/approve",
        json={"decision": "APPROVE", "rationale": "independent review"},
        headers={**BOB_APPROVER, "Idempotency-Key": "cross-b2"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"


def test_triage_requester_cannot_approve(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    # Carol owns the case; Alice requests triage as analyst.
    case_id = _create_case(client, headers=CAROL_ANALYST)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["triage_requested_by"] == "user:alice"
    # Alice's approver role is denied: she requested triage.
    denied = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "requester-1"},
    )
    assert denied.status_code == 403
    reasons = denied.json()["error"]["details"]["reason_codes"]
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in reasons
    assert "SELF_APPROVAL_DENIED" not in reasons
    # Bob can approve.
    approved = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "independent"},
        headers={**BOB_APPROVER, "Idempotency-Key": "requester-2"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"


def test_end_to_end_approval(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["status"] == "PENDING_APPROVAL"
    approve = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "looks good"},
        headers={**BOB_APPROVER, "Idempotency-Key": "key-1"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["case"]["status"] == "EXECUTED"
    case = client.get(f"/v1/cases/{case_id}", headers=ALICE_ANALYST)
    assert case.status_code == 200
    assert case.json()["execution"]["status"] == "SUCCEEDED"
    audit = client.get(f"/v1/cases/{case_id}/audit", headers=ALICE_ANALYST)
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
    response = client.post("/v1/cases", json={}, headers=ALICE_ANALYST)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    provided = client.get("/healthz", headers={"X-Request-ID": "req-123"})
    assert provided.headers["X-Request-ID"] == "req-123"
    oversized = client.post("/v1/cases", content=b"x" * 70000, headers=ALICE_ANALYST)
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_not_found(client: TestClient) -> None:
    response = client.get("/v1/cases/CASE-X", headers=ALICE_ANALYST)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CASE_NOT_FOUND"
    audit = client.get("/v1/cases/CASE-X/audit", headers=ALICE_ANALYST)
    assert audit.status_code == 404
    assert audit.json()["error"]["code"] == "CASE_NOT_FOUND"


def test_self_approval_api(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    assert triage.status_code == 200, triage.text
    assert triage.json()["case"]["triage_requested_by"] == "user:alice"
    response = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "key-1"},
    )
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "APPROVAL_DENIED"
    assert "SELF_APPROVAL_DENIED" in body["details"]["reason_codes"]
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in body["details"]["reason_codes"]
    approved = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "independent review"},
        headers={**BOB_APPROVER, "Idempotency-Key": "key-2"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["case"]["status"] == "EXECUTED"


def test_audit_pagination(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=25000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    first = client.get(
        f"/v1/cases/{case_id}/audit?limit=1&offset=0", headers=ALICE_ANALYST
    )
    assert first.status_code == 200
    body = first.json()
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    second = client.get(
        f"/v1/cases/{case_id}/audit?limit=1&offset=1", headers=ALICE_ANALYST
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["id"] != body["items"][0]["id"]
    default = client.get(f"/v1/cases/{case_id}/audit", headers=ALICE_ANALYST)
    assert default.status_code == 200
    assert default.json()["limit"] == 500
    assert default.json()["offset"] == 0
    zero = client.get(f"/v1/cases/{case_id}/audit?limit=0", headers=ALICE_ANALYST)
    assert zero.status_code == 422
    negative = client.get(
        f"/v1/cases/{case_id}/audit?offset=-1", headers=ALICE_ANALYST
    )
    assert negative.status_code == 422
    max_page = client.get(
        f"/v1/cases/{case_id}/audit?limit=1000", headers=ALICE_ANALYST
    )
    assert max_page.status_code == 200
    assert max_page.json()["limit"] == 1000
    past = client.get(
        f"/v1/cases/{case_id}/audit?offset=9999", headers=ALICE_ANALYST
    )
    assert past.status_code == 200
    assert past.json()["items"] == []
    assert past.json()["offset"] == 9999
    too_large = client.get(
        f"/v1/cases/{case_id}/audit?limit=1001", headers=ALICE_ANALYST
    )
    assert too_large.status_code == 422


def test_self_reject_denied(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    response = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "REJECT", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "reject-1"},
    )
    assert response.status_code == 403
    reasons = response.json()["error"]["details"]["reason_codes"]
    assert "SELF_APPROVAL_DENIED" in reasons
    assert "TRIAGE_REQUESTER_CANNOT_APPROVE" in reasons
    case = client.get(f"/v1/cases/{case_id}", headers=ALICE_ANALYST)
    assert case.json()["case"]["status"] == "PENDING_APPROVAL"


def test_status_modes_and_counters(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "mine"},
        headers={**ALICE_APPROVER, "Idempotency-Key": "status-denial"},
    )
    status = client.get("/status").json()
    assert status["approval_denials"] == 1
    assert status["mode"] == "normal"

    degraded = load_settings(
        {
            "DATABASE_PATH": str(settings.database_path),
            "POLICY_PATH": str(settings.policy_path),
            "DEGRADED_MODE": "true",
        }
    )
    with TestClient(create_app(degraded)) as local:
        assert local.get("/status").json()["mode"] == "degraded"

    disabled = load_settings(
        {
            "DATABASE_PATH": str(settings.database_path),
            "POLICY_PATH": str(settings.policy_path),
            "AUTO_APPROVE_ENABLED": "false",
        }
    )
    with TestClient(create_app(disabled)) as local:
        assert local.get("/status").json()["mode"] == "auto_approve_disabled"


def test_unhandled_exception_envelope(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr("payment_triage.api.Workflow.list_case_views", boom)
    with TestClient(
        create_app(settings), raise_server_exceptions=False
    ) as local:
        response = local.get(
            "/v1/cases",
            headers={**ALICE_ANALYST, "Origin": "http://localhost:5173"},
        )
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "INTERNAL_ERROR"
    assert body["request_id"]
    assert response.headers["X-Request-ID"]
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )


def test_create_case_errors(client: TestClient, settings: Settings) -> None:
    missing = client.post(
        "/v1/cases",
        json={"payment_id": "PAY-X", "exception_type": "FAILED_PAYMENT"},
        headers=ALICE_ANALYST,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PAYMENT_NOT_FOUND"

    _seed_payment(settings, amount=25000)
    created = client.post(
        "/v1/cases",
        json={"payment_id": "PAY-1", "exception_type": "FAILED_PAYMENT"},
        headers=ALICE_ANALYST,
    )
    assert created.status_code == 201
    duplicate = client.post(
        "/v1/cases",
        json={"payment_id": "PAY-1", "exception_type": "FAILED_PAYMENT"},
        headers=ALICE_ANALYST,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CASE_EXISTS"
    mismatch = client.post(
        "/v1/cases",
        json={"payment_id": "PAY-1", "exception_type": "DUPLICATE_PAYMENT"},
        headers=ALICE_ANALYST,
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "EXCEPTION_TYPE_MISMATCH"
    for query in ("limit=0", "limit=101", "status=BOGUS"):
        response = client.get(f"/v1/cases?{query}", headers=ALICE_ANALYST)
        assert response.status_code == 422
    listed = client.get("/v1/cases?limit=1&offset=0", headers=ALICE_ANALYST)
    assert listed.status_code == 200
    assert listed.json()["limit"] == 1
    assert listed.json()["offset"] == 0


def test_api_ui_contract(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    triage = client.post(f"/v1/cases/{case_id}/triage", headers=ALICE_ANALYST)
    body = triage.json()
    assert body["case"]["triage_requested_by"] == "user:alice"
    assert "evidence" in body["proposal"]
    assert "requires_approval" in body["proposal"]
    assert body["proposal"]["planner_version"]
    approved = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": "contract"},
        headers={**BOB_APPROVER, "Idempotency-Key": "contract-1"},
    )
    assert approved.status_code == 200
    assert approved.json()["approval"]["rationale_redacted"]
    audit = client.get(f"/v1/cases/{case_id}/audit", headers=ALICE_ANALYST).json()
    assert {"items", "limit", "offset"} <= set(audit)
    assert audit["items"][0]["actor_role"]
    status = client.get("/status").json()
    for key in (
        "mode",
        "auto_retry_limit_cents",
        "refund_cap_cents",
        "auto_approve_enabled",
        "degraded_mode",
    ):
        assert key in status
    listed = client.get("/v1/cases", headers=ALICE_ANALYST).json()
    assert {"items", "limit", "offset"} <= set(listed)


def test_cors_preflight(client: TestClient) -> None:
    response = client.options(
        "/v1/cases",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )


def test_root_redirect(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] in {"/ui/", "/docs"}


@pytest.mark.skipif(not UI_DIST.is_dir(), reason="UI not built")
def test_ui_served_when_built(client: TestClient) -> None:
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Payment Exception Triage" in response.text


@pytest.mark.skipif(not UI_DIST.is_dir(), reason="UI not built")
def test_ui_assets_served_when_built(client: TestClient) -> None:
    index = client.get("/ui/")
    assert index.status_code == 200
    assets = re.findall(r'(?:src|href)="(/ui/assets/[^"]+)"', index.text)
    assert assets, "built UI should reference hashed assets"
    for path in assets:
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert asset.content, path


def test_idempotency_api(client: TestClient, settings: Settings) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    body = {"decision": "APPROVE", "rationale": "ok"}
    headers = {**BOB_APPROVER, "Idempotency-Key": "key-1"}
    first = client.post(f"/v1/cases/{case_id}/approve", json=body, headers=headers)
    second = client.post(f"/v1/cases/{case_id}/approve", json=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["case"]["status"] == "EXECUTED"
    audit = client.get(f"/v1/cases/{case_id}/audit", headers=ALICE_ANALYST).json()
    assert "IDEMPOTENT_REPLAY" in [item["event_type"] for item in audit["items"]]
    missing = client.post(
        f"/v1/cases/{case_id}/approve", json=body, headers=BOB_APPROVER
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_create_case_rejects_client_assignee(
    client: TestClient, settings: Settings
) -> None:
    _seed_payment(settings, amount=25000)
    response = client.post(
        "/v1/cases",
        json={
            "payment_id": "PAY-1",
            "exception_type": "FAILED_PAYMENT",
            "assignee": "user:carol",
        },
        headers=ALICE_ANALYST,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_readyz_missing_schema(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("payment_triage.api.init_schema", lambda conn: None)
    with TestClient(create_app(settings)) as local_client:
        response = local_client.get("/readyz")
    assert response.status_code == 503


def test_readyz_missing_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as local_client:
        settings.database_path.unlink()
        assert local_client.get("/readyz").status_code == 503


def test_missing_policy_startup_fails(tmp_path: Path) -> None:
    bad = load_settings(
        {
            "DATABASE_PATH": str(tmp_path / "bad.db"),
            "POLICY_PATH": str(tmp_path / "missing.json"),
        }
    )
    with pytest.raises(FileNotFoundError):
        with TestClient(create_app(bad)):
            pass


def test_chunked_request_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/cases",
        content=iter([b"x"]),
        headers={**ALICE_ANALYST, "Transfer-Encoding": "chunked"},
    )
    assert response.status_code == 411
    assert response.json()["error"]["code"] == "LENGTH_REQUIRED"


def test_oversized_request_has_cors_headers(client: TestClient) -> None:
    response = client.post(
        "/v1/cases",
        content=b"x" * 70000,
        headers={**ALICE_ANALYST, "Origin": "http://localhost:5173"},
    )
    assert response.status_code == 413
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )


def test_long_rationale_redaction_truncated(
    client: TestClient, settings: Settings
) -> None:
    _seed_payment(settings, amount=500000)
    case_id = _create_case(client)
    client.post(f"/v1/cases/{case_id}/triage", headers=AGENT)
    rationale = ("a@b.c " * 333).strip()
    response = client.post(
        f"/v1/cases/{case_id}/approve",
        json={"decision": "APPROVE", "rationale": rationale},
        headers={**BOB_APPROVER, "Idempotency-Key": "key-long"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["approval"]["rationale_redacted"]) <= 2000
