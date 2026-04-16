"""
Tests for the low-side /user/send form handler.

Guards against schema drift between the low-side UI form handler and the
low-side Message model. Low-side messages use TestID/Area/Date/Status —
any form still submitting old-style "Test ID"/"Timestamp"/"Test Status"
must be caught by these tests.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_user_client(tmp_path, monkeypatch):
    """
    Set up the low-side app with:
      - temp users file, pre-populated with 'tester'
      - mocked gateway_client so sends don't leave the process
      - active session cookie for 'tester'
    """
    from app import main, auth, user as user_module

    users_path = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_FILE_PATH", users_path)

    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
            }
        }
    )

    class _StubGateway:
        async def send_message(self, message_data):
            return {"success": True, "message_id": message_data.get("ID")}

    main.gateway_client = _StubGateway()
    user_module.set_gateway_client(main.gateway_client)

    client = TestClient(main.app)
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)

    return client


def _aligned_payload():
    return {
        "message_id": str(uuid.uuid4()),
        "project": "AAA",
        "test_id": "TST001",
        "area": "Integration",
        "timestamp": "2026-01-30T11:22:33",
        "test_status": "Inprogress",
        "data_json": json.dumps({"result": "pass"}),
    }


def test_send_accepts_aligned_schema(configured_user_client):
    """Form submit with aligned schema succeeds."""
    client = configured_user_client
    r = client.post("/user/send", data=_aligned_payload(), follow_redirects=False)

    assert r.status_code in (200, 303), f"unexpected status: {r.status_code} {r.text[:200]}"
    if r.status_code == 303:
        assert "error=" not in r.headers.get("location", ""), \
            f"unexpected error redirect: {r.headers.get('location')}"


def test_send_rejects_missing_area(configured_user_client):
    """Area is required — submitting without it must fail (HTTP 422)."""
    client = configured_user_client
    payload = _aligned_payload()
    del payload["area"]
    r = client.post("/user/send", data=payload, follow_redirects=False)
    assert r.status_code == 422


def test_send_rejects_bad_date_format(configured_user_client):
    """Non-ISO-8601 date fails schema validation."""
    client = configured_user_client
    payload = _aligned_payload()
    payload["timestamp"] = "30012026T11:22:33"
    r = client.post("/user/send", data=payload, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error=" in location, f"expected error redirect, got: {location}"


def test_send_rejects_invalid_project(configured_user_client):
    """Project must be 3 uppercase alphanumeric chars."""
    client = configured_user_client
    payload = _aligned_payload()
    payload["project"] = "ZZ"  # too short
    r = client.post("/user/send", data=payload, follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", "")
