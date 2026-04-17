"""
Tests for the corporate audit log (failed login tracking).

Covers:
  - audit.record_failed_login writes + reads back correctly
  - auth.classify_login_failure produces expected reasons
  - /admin/login and /user/login submit handlers record failures
  - /dmz/audit/failed-login receives forwarded events from the low-side
  - /admin/audit page renders recent events
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """
    Isolate audit + user storage per-test.

    The audit module computes its directory from
    `config.master_dir.parent / "audit"`, so we point MASTER_DIR at a
    temp path before the audit module resolves anything.
    """
    from app import config as config_module, auth

    config_module.config._config["MASTER_DIR"] = str(tmp_path / "messages")
    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")

    # Seed a known admin + user for the login-handler tests
    auth._save_users(
        {
            "rootadmin": {
                "password_hash": auth._hash_password("AdminPass1!abc"),
                "role": "admin",
                "enabled": True,
                "must_change_password": False,
            },
            "alice": {
                "password_hash": auth._hash_password("UserPass123!"),
                "role": "user",
                "enabled": True,
                "must_change_password": False,
            },
            "dormant": {
                "password_hash": auth._hash_password("UserPass123!"),
                "role": "user",
                "enabled": False,
                "must_change_password": False,
            },
        }
    )
    yield tmp_path


def _read_today_events(tmp_path):
    from datetime import datetime

    day = datetime.now().strftime("%Y-%m-%d")
    path = tmp_path / "audit" / "failed_logins" / f"{day}.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# -------------------- audit module --------------------


def test_record_failed_login_persists_event(audit_env):
    from app import audit

    event = audit.record_failed_login(
        username="alice", source="corporate-user", reason="bad_password"
    )
    assert event["username"] == "alice"
    assert event["source"] == "corporate-user"

    events = _read_today_events(audit_env)
    assert len(events) == 1
    assert events[0]["username"] == "alice"


def test_record_failed_login_normalises_unknown_source(audit_env):
    from app import audit

    event = audit.record_failed_login(
        username="x", source="bogus-source", reason="bad_password"
    )
    assert event["source"] == "unknown"


def test_list_recent_returns_newest_first(audit_env):
    from app import audit

    audit.record_failed_login("first", "corporate-user", "bad_password")
    audit.record_failed_login("second", "corporate-user", "bad_password")
    audit.record_failed_login("third", "corporate-user", "bad_password")

    events = audit.list_recent_failed_logins(limit=10)
    # Written in order first/second/third; newest first means third is at index 0
    assert [e["username"] for e in events][:3] == ["third", "second", "first"]


# -------------------- auth.classify_login_failure --------------------


def test_classify_user_not_found(audit_env):
    from app import auth

    assert auth.classify_login_failure("ghost", role="user") == "user_not_found"


def test_classify_wrong_role(audit_env):
    from app import auth

    # alice is a user; admin login attempt -> wrong_role
    assert auth.classify_login_failure("alice", role="admin") == "wrong_role"


def test_classify_user_disabled(audit_env):
    from app import auth

    assert auth.classify_login_failure("dormant", role="user") == "user_disabled"


def test_classify_bad_password(audit_env):
    from app import auth

    # alice exists, enabled, correct role → classification is bad_password
    assert auth.classify_login_failure("alice", role="user") == "bad_password"


# -------------------- login submit handlers --------------------


def test_corporate_user_login_failure_recorded(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.post(
        "/user/login",
        data={"username": "alice", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    events = _read_today_events(audit_env)
    assert any(
        e["source"] == "corporate-user" and e["username"] == "alice"
        for e in events
    )


def test_corporate_admin_login_failure_recorded(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.post(
        "/admin/login",
        data={"username": "rootadmin", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    events = _read_today_events(audit_env)
    assert any(
        e["source"] == "corporate-admin" and e["username"] == "rootadmin"
        for e in events
    )


def test_successful_login_does_not_record(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.post(
        "/user/login",
        data={"username": "alice", "password": "UserPass123!"},
        follow_redirects=False,
    )
    # Login succeeded
    assert r.status_code == 303 and "error=" not in r.headers.get("location", "")

    events = _read_today_events(audit_env)
    assert events == []


# -------------------- /dmz/audit/failed-login receiver --------------------


def test_dmz_audit_receiver_records_forwarded_event(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.post(
        "/dmz/audit/failed-login",
        json={
            "username": "bob",
            "source": "low-side",
            "reason": "bad_password",
            "timestamp": "2026-04-17T10:00:00",
        },
    )
    assert r.status_code == 200

    events = _read_today_events(audit_env)
    forwarded = [e for e in events if e["source"] == "low-side"]
    assert len(forwarded) == 1
    assert forwarded[0]["username"] == "bob"
    # Original timestamp preserved
    assert forwarded[0]["timestamp"] == "2026-04-17T10:00:00"


def test_dmz_audit_receiver_rejects_bad_json(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.post(
        "/dmz/audit/failed-login",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


# -------------------- admin audit page --------------------


def test_admin_audit_page_requires_auth(audit_env):
    from app import main

    client = TestClient(main.app)
    r = client.get("/admin/audit", follow_redirects=False)
    assert r.status_code == 303
    assert "/admin/login" in r.headers.get("location", "")


def test_admin_audit_page_renders_events(audit_env):
    from app import main, auth, audit

    # Seed an event
    audit.record_failed_login("alice", "corporate-user", "bad_password")

    client = TestClient(main.app)
    token = auth.create_admin_session("rootadmin")
    client.cookies.set("admin_session", token)

    r = client.get("/admin/audit")
    assert r.status_code == 200
    assert "alice" in r.text
    assert "Corporate User" in r.text or "corporate-user" in r.text
