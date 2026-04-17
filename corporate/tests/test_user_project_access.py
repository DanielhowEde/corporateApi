"""
Tests for per-user project access control (corporate side).

Covers:
  - auth helpers (get/set/user_can_send_to_project)
  - admin route to set allowed_projects
  - user send page filters dropdown by user's allowed list
  - user send handler rejects projects not in user's allowed list
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_user_client(tmp_path, monkeypatch):
    """Corporate app with a logged-in user + whitelist with AAA, BBB, CCC."""
    from app import main, auth, user as user_module
    from app.whitelist import ProjectWhitelist
    from app.file_store import FileStore

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                "role": "user",
                "allowed_projects": ["AAA"],  # tester can only send to AAA
            }
        }
    )

    wl = ProjectWhitelist(file_path=str(tmp_path / "whitelist.json"))
    for code in ("AAA", "BBB", "CCC"):
        wl.add_project(code)

    main.whitelist = wl
    main.file_store = FileStore(
        master_dir=str(tmp_path / "messages"),
        tmp_dir=str(tmp_path / "tmp"),
        error_dir=str(tmp_path / "errors"),
    )
    user_module.set_whitelist(wl)
    user_module.set_file_store(main.file_store)

    class _StubGateway:
        async def send_message(self, message_data, auto_send=True):
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
        "auto_send": "on",
    }


# -------------------- auth helpers --------------------


def test_get_user_allowed_projects_returns_list(configured_user_client):
    from app import auth

    assert auth.get_user_allowed_projects("tester") == ["AAA"]
    assert auth.get_user_allowed_projects("nonexistent") == []


def test_user_can_send_to_project_positive_and_negative(configured_user_client):
    from app import auth

    assert auth.user_can_send_to_project("tester", "AAA") is True
    assert auth.user_can_send_to_project("tester", "aaa") is True  # case insensitive
    assert auth.user_can_send_to_project("tester", "BBB") is False


def test_set_user_allowed_projects_updates_list(configured_user_client):
    from app import auth

    ok, msg = auth.set_user_allowed_projects("tester", ["bbb", "CCC", " "])
    assert ok is True
    # Codes normalised to uppercase, blanks dropped, sorted
    assert auth.get_user_allowed_projects("tester") == ["BBB", "CCC"]


def test_set_user_allowed_projects_missing_user(configured_user_client):
    from app import auth

    ok, msg = auth.set_user_allowed_projects("ghost", ["AAA"])
    assert ok is False
    assert "not found" in msg.lower()


# -------------------- send page UI --------------------


def test_send_page_dropdown_only_shows_allowed_projects(configured_user_client):
    r = configured_user_client.get("/user/send")
    assert r.status_code == 200
    # Only AAA is in the user's allowed list — BBB and CCC must NOT appear as options
    assert '<option value="AAA">AAA</option>' in r.text
    assert '<option value="BBB">BBB</option>' not in r.text
    assert '<option value="CCC">CCC</option>' not in r.text


def test_send_page_shows_warning_when_no_projects(tmp_path, monkeypatch):
    """User with empty allowed_projects sees a warning banner."""
    from app import main, auth, user as user_module
    from app.whitelist import ProjectWhitelist
    from app.file_store import FileStore

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "nobody": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                "role": "user",
                # no allowed_projects -> empty list
            }
        }
    )
    wl = ProjectWhitelist(file_path=str(tmp_path / "whitelist.json"))
    wl.add_project("AAA")
    main.whitelist = wl
    main.file_store = FileStore(
        master_dir=str(tmp_path / "m"),
        tmp_dir=str(tmp_path / "t"),
        error_dir=str(tmp_path / "e"),
    )
    user_module.set_whitelist(wl)
    user_module.set_file_store(main.file_store)

    client = TestClient(main.app)
    token = auth.create_user_session("nobody")
    client.cookies.set("session_token", token)

    r = client.get("/user/send")
    assert r.status_code == 200
    assert "No projects authorised" in r.text


# -------------------- send submit enforcement --------------------


def test_send_rejects_project_user_not_granted(configured_user_client):
    """BBB is whitelisted but tester doesn't have it — must be rejected."""
    r = configured_user_client.post(
        "/user/send", data=_payload("BBB"), follow_redirects=False
    )
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error=" in location
    assert "not+authorised" in location or "not+authorized" in location


def test_send_accepts_project_user_is_granted(configured_user_client):
    """AAA is in tester's allowed list — must succeed."""
    r = configured_user_client.post(
        "/user/send", data=_payload("AAA"), follow_redirects=False
    )
    assert r.status_code in (200, 303)
    if r.status_code == 303:
        assert "error=" not in r.headers.get("location", "")


# -------------------- admin route --------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """Corporate app with a logged-in admin + test user + whitelist."""
    from app import main, auth, admin as admin_module
    from app.whitelist import ProjectWhitelist
    from app.file_store import FileStore

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "rootadmin": {
                "password_hash": auth._hash_password("adminpw"),
                "enabled": True,
                "must_change_password": False,
                "role": "admin",
            },
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
                "role": "user",
                "allowed_projects": [],
            },
        }
    )

    wl = ProjectWhitelist(file_path=str(tmp_path / "whitelist.json"))
    for code in ("AAA", "BBB", "CCC"):
        wl.add_project(code)

    main.whitelist = wl
    main.file_store = FileStore(
        master_dir=str(tmp_path / "m"),
        tmp_dir=str(tmp_path / "t"),
        error_dir=str(tmp_path / "e"),
    )
    admin_module.set_whitelist(wl)
    # No gateway_client — sync_user becomes a no-op

    client = TestClient(main.app)
    token = auth.create_admin_session("rootadmin")
    client.cookies.set("admin_session", token)
    return client


def test_admin_can_set_user_allowed_projects(admin_client):
    from app import auth

    # Initial state: empty list
    assert auth.get_user_allowed_projects("tester") == []

    r = admin_client.post(
        "/admin/users/tester/projects",
        data={"allowed": ["AAA", "BBB"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "message=" in r.headers.get("location", "")

    assert auth.get_user_allowed_projects("tester") == ["AAA", "BBB"]


def test_admin_can_clear_user_allowed_projects(admin_client):
    from app import auth

    auth.set_user_allowed_projects("tester", ["AAA"])
    assert auth.get_user_allowed_projects("tester") == ["AAA"]

    # Submit with no checkboxes = empty list
    r = admin_client.post(
        "/admin/users/tester/projects", data={}, follow_redirects=False
    )
    assert r.status_code == 303
    assert auth.get_user_allowed_projects("tester") == []


def test_admin_users_page_shows_allowed_projects(admin_client):
    from app import auth

    auth.set_user_allowed_projects("tester", ["AAA", "CCC"])
    r = admin_client.get("/admin/users")
    assert r.status_code == 200
    # Both granted codes appear in the user row
    assert "AAA" in r.text
    assert "CCC" in r.text
