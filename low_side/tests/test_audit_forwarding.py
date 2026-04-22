"""
Tests for low-side audit forwarding.

When a login fails on the low-side, `gateway_client.report_failed_login`
must be called with the attempted username + classified reason so
corporate can persist the event centrally.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def audit_client(tmp_path, monkeypatch):
    """Low-side app with seeded users + a mock gateway_client."""
    from app import auth, main
    from app import user as user_module

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "alice": {
                "password_hash": auth._hash_password("UserPass123!"),
                "enabled": True,
                "must_change_password": False,
            },
            "dormant": {
                "password_hash": auth._hash_password("UserPass123!"),
                "enabled": False,
                "must_change_password": False,
            },
        }
    )

    calls = []

    class _SpyGateway:
        async def report_failed_login(self, username, reason="bad_password"):
            calls.append({"username": username, "reason": reason})

        async def send_message(self, message_data):
            return {"success": True}

    spy = _SpyGateway()
    monkeypatch.setattr(main, "gateway_client", spy, raising=False)
    user_module.set_gateway_client(spy)
    return TestClient(main.app), calls


def test_failed_login_reports_to_gateway(audit_client):
    client, calls = audit_client
    r = client.post(
        "/user/login",
        data={"username": "alice", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(calls) == 1
    assert calls[0]["username"] == "alice"
    assert calls[0]["reason"] == "bad_password"


def test_unknown_user_report_has_user_not_found_reason(audit_client):
    client, calls = audit_client
    client.post(
        "/user/login",
        data={"username": "ghost", "password": "whatever"},
        follow_redirects=False,
    )
    assert len(calls) == 1
    assert calls[0]["reason"] == "user_not_found"


def test_disabled_user_report_has_user_disabled_reason(audit_client):
    client, calls = audit_client
    client.post(
        "/user/login",
        data={"username": "dormant", "password": "UserPass123!"},
        follow_redirects=False,
    )
    assert len(calls) == 1
    assert calls[0]["reason"] == "user_disabled"


def test_successful_login_does_not_report(audit_client):
    client, calls = audit_client
    r = client.post(
        "/user/login",
        data={"username": "alice", "password": "UserPass123!"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "error=" not in r.headers.get("location", "")
    assert calls == []


def test_gateway_failure_does_not_block_login_flow(tmp_path, monkeypatch):
    """If the gateway report raises, the login flow must still return the
    normal redirect (auth is never blocked by audit pipeline outages)."""
    from app import auth, main
    from app import user as user_module

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "alice": {
                "password_hash": auth._hash_password("UserPass123!"),
                "enabled": True,
                "must_change_password": False,
            }
        }
    )

    class _BrokenGateway:
        async def report_failed_login(self, username, reason="bad_password"):
            raise RuntimeError("gateway unreachable")

    broken = _BrokenGateway()
    monkeypatch.setattr(main, "gateway_client", broken, raising=False)
    user_module.set_gateway_client(broken)

    client = TestClient(main.app)
    r = client.post(
        "/user/login",
        data={"username": "alice", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", "")
