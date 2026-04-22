"""
Tests for per-user project access control (low-side).

Low-side has no global whitelist — the user's `allowed_projects` list
(synced from corporate) is the sole gate on what they can send.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_user_client(tmp_path, monkeypatch):
    from app import auth, main
    from app import user as user_module

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                "allowed_projects": ["AAA"],
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


def _payload(project: str) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "project": project,
        "test_id": "TST001",
        "area": "Integration",
        "timestamp": "2026-01-30T11:22:33",
        "test_status": "Inprogress",
        "data_json": json.dumps({"result": "pass"}),
    }


# -------------------- auth helpers --------------------


def test_allowed_projects_exposed_via_helpers(configured_user_client):
    from app import auth

    assert auth.get_user_allowed_projects("tester") == ["AAA"]
    assert auth.user_can_send_to_project("tester", "AAA") is True
    assert auth.user_can_send_to_project("tester", "BBB") is False


# -------------------- send page UI --------------------


def test_send_page_dropdown_only_shows_allowed_projects(configured_user_client):
    r = configured_user_client.get("/user/send")
    assert r.status_code == 200
    assert '<option value="AAA">AAA</option>' in r.text
    assert '<option value="ZZZ">ZZZ</option>' not in r.text


def test_send_page_shows_warning_when_no_projects(tmp_path, monkeypatch):
    from app import auth, main

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "nobody": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                # no allowed_projects
            }
        }
    )

    client = TestClient(main.app)
    token = auth.create_user_session("nobody")
    client.cookies.set("session_token", token)

    r = client.get("/user/send")
    assert r.status_code == 200
    assert "No projects authorised" in r.text


# -------------------- send submit enforcement --------------------


def test_send_rejects_project_user_not_granted(configured_user_client):
    r = configured_user_client.post("/user/send", data=_payload("BBB"), follow_redirects=False)
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error=" in location
    assert "not+authorised" in location or "not+authorized" in location


def test_send_accepts_project_user_is_granted(configured_user_client):
    r = configured_user_client.post("/user/send", data=_payload("AAA"), follow_redirects=False)
    assert r.status_code == 303
    assert "error=" not in r.headers.get("location", "")


# -------------------- sync payload handling --------------------


def test_sync_persists_allowed_projects(tmp_path, monkeypatch):
    """/dmz/users sync should round-trip allowed_projects."""
    from app import auth, main

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")

    client = TestClient(main.app)

    r = client.post(
        "/dmz/users",
        json={
            "username": "fromsync",
            "action": "upsert",
            "password_hash": "fakehash",
            "enabled": True,
            "must_change_password": False,
            "allowed_projects": ["aaa", "BBB", " "],  # mixed-case + blank
        },
    )
    assert r.status_code == 200

    # Normalised to uppercase, blanks dropped, sorted
    assert auth.get_user_allowed_projects("fromsync") == ["AAA", "BBB"]


def test_sync_without_allowed_projects_preserves_existing(tmp_path, monkeypatch):
    """If payload omits allowed_projects, existing list must be preserved."""
    from app import auth, main

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "alice": {
                "password_hash": "x",
                "enabled": True,
                "must_change_password": False,
                "allowed_projects": ["AAA"],
            }
        }
    )

    client = TestClient(main.app)
    r = client.post(
        "/dmz/users",
        json={
            "username": "alice",
            "action": "upsert",
            "password_hash": "x",
            "enabled": True,
            "must_change_password": False,
            # No allowed_projects key
        },
    )
    assert r.status_code == 200
    assert auth.get_user_allowed_projects("alice") == ["AAA"]
