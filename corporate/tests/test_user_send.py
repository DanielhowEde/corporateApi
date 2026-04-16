"""
Tests for the corporate /user/send form handler.

These tests guard against schema drift between the UI form handler and
the Message model. They log in a test user, submit the form with aligned
schema field names (TestID/Area/Date/Status) and with the old schema,
and verify only the aligned form is accepted.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_user_client(tmp_path, monkeypatch):
    """
    Set up the corporate app with:
      - temp users file with a pre-logged-in user
      - whitelist containing AAA
      - mocked gateway/cert clients so sends don't leave the process
    """
    from app import main, auth, user as user_module
    from app.whitelist import ProjectWhitelist
    from app.file_store import FileStore

    # Point auth at a temp users file
    users_path = tmp_path / "users.json"
    monkeypatch.setattr(auth, "USERS_FILE_PATH", users_path)

    # Write a user so we can create a session without going through login flow
    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                "role": "user",
            }
        }
    )

    # Set up whitelist + file_store
    whitelist_path = tmp_path / "whitelist.json"
    main.whitelist = ProjectWhitelist(file_path=str(whitelist_path))
    main.whitelist.add_project("AAA")

    master_dir = tmp_path / "messages"
    tmp_dir = tmp_path / "tmp"
    error_dir = tmp_path / "errors"
    main.file_store = FileStore(
        master_dir=str(master_dir),
        tmp_dir=str(tmp_dir),
        error_dir=str(error_dir),
    )

    # Wire the user module's shared state
    user_module.set_whitelist(main.whitelist)
    user_module.set_file_store(main.file_store)

    # Stub gateway_client so the send doesn't actually try to reach a gateway
    class _StubGateway:
        async def send_message(self, message_data, auto_send=True):
            return {"success": True, "message_id": message_data.get("ID")}

    main.gateway_client = _StubGateway()
    user_module.set_gateway_client(main.gateway_client)

    client = TestClient(main.app)

    # Create a session for "tester"
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)

    return client


def _aligned_payload(project="AAA"):
    return {
        "message_id": str(uuid.uuid4()),
        "project": project,
        "test_id": "TST001",
        "area": "Integration",
        "timestamp": "2026-01-30T11:22:33",
        "test_status": "Inprogress",
        "data_json": json.dumps({"result": "pass"}),
        "auto_send": "on",
    }


def test_send_accepts_aligned_schema(configured_user_client):
    """Form submit with aligned schema field names succeeds."""
    client = configured_user_client
    r = client.post("/user/send", data=_aligned_payload(), follow_redirects=False)

    # Success = either a 200 render or a 303 redirect with `message=...`
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
    assert r.status_code == 422  # FastAPI form validation


def test_send_rejects_non_whitelisted_project(configured_user_client):
    """Non-whitelisted project triggers redirect with error."""
    client = configured_user_client
    r = client.post(
        "/user/send",
        data=_aligned_payload(project="ZZZ"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", ""), r.headers.get("location")


def test_send_rejects_bad_date_format(configured_user_client):
    """A non-ISO-8601 date should fail schema validation, not succeed silently."""
    client = configured_user_client
    payload = _aligned_payload()
    payload["timestamp"] = "30012026T11:22:33"  # old dd-mm-yyyy format
    r = client.post("/user/send", data=payload, follow_redirects=False)

    # Must surface as a redirect with validation error
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error=" in location, f"expected error redirect, got: {location}"
