"""Tests for the /user/history/clear endpoint (low-side)."""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    """Set up low-side with a temp file store + logged-in user."""
    from app import auth, main
    from app import user as user_module
    from app.file_store import FileStore

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("pw12345"),
                "enabled": True,
                "must_change_password": False,
            }
        }
    )

    fs = FileStore(
        master_dir=str(tmp_path / "messages"),
        tmp_dir=str(tmp_path / "tmp"),
        error_dir=str(tmp_path / "errors"),
    )
    main.file_store = fs
    user_module.set_file_store(fs)

    client = TestClient(main.app)
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)
    return client, fs


def _seed(fs, project: str, mtime_offset_days: float = 0) -> str:
    msg_id = str(uuid.uuid4())
    msg = {
        "ID": msg_id,
        "Project": project,
        "TestID": "TST001",
        "Area": "Unit",
        "Date": "2026-01-30T11:22:33",
        "Status": "Complete",
        "Data": {"k": "v"},
    }
    path = fs.write_message(msg)
    if mtime_offset_days:
        past = time.time() - (mtime_offset_days * 86400)
        os.utime(path, (past, past))
    return msg_id


def test_clear_all(history_client):
    client, fs = history_client
    _seed(fs, "AAA")
    _seed(fs, "BBB")
    assert len(fs.get_all_messages()) == 2

    r = client.post("/user/history/clear", data={"older_than_days": 0}, follow_redirects=False)
    assert r.status_code == 303
    assert "Cleared+2+message" in r.headers.get("location", "")
    assert fs.get_all_messages() == []


def test_clear_only_old(history_client):
    client, fs = history_client
    fresh_id = _seed(fs, "AAA", mtime_offset_days=0)
    _seed(fs, "AAA", mtime_offset_days=10)

    r = client.post("/user/history/clear", data={"older_than_days": 7}, follow_redirects=False)
    assert r.status_code == 303
    assert "Cleared+1+message" in r.headers.get("location", "")

    remaining = fs.get_all_messages()
    assert len(remaining) == 1
    assert remaining[0]["ID"] == fresh_id


def test_clear_no_matches(history_client):
    client, fs = history_client
    _seed(fs, "AAA")

    r = client.post("/user/history/clear", data={"older_than_days": 30}, follow_redirects=False)
    assert r.status_code == 303
    assert "Cleared+0+message" in r.headers.get("location", "")
    assert len(fs.get_all_messages()) == 1


def test_clear_requires_auth(history_client):
    client, _ = history_client
    client.cookies.clear()
    r = client.post("/user/history/clear", data={"older_than_days": 0}, follow_redirects=False)
    assert r.status_code == 303
    assert "/user/login" in r.headers.get("location", "")


def test_history_page_shows_retention_default(history_client):
    client, _ = history_client
    r = client.get("/user/history")
    assert r.status_code == 200
    assert 'value="7"' in r.text
    assert "Clear History" in r.text


def test_history_pagination_first_page(history_client):
    client, fs = history_client
    for _ in range(25):
        _seed(fs, "AAA")

    r = client.get("/user/history")
    assert r.status_code == 200
    assert "Page <strong>1</strong> of <strong>2</strong>" in r.text
    assert "page=2" in r.text


def test_history_pagination_second_page(history_client):
    client, fs = history_client
    for _ in range(25):
        _seed(fs, "AAA")

    r = client.get("/user/history?page=2")
    assert r.status_code == 200
    assert "Page <strong>2</strong> of <strong>2</strong>" in r.text
    assert "page=1" in r.text


def test_history_pagination_clamps_invalid_page(history_client):
    client, fs = history_client
    for _ in range(25):
        _seed(fs, "AAA")

    r = client.get("/user/history?page=999")
    assert r.status_code == 200
    assert "Page <strong>2</strong> of <strong>2</strong>" in r.text

    r = client.get("/user/history?page=0")
    assert r.status_code == 200
    assert "Page <strong>1</strong> of <strong>2</strong>" in r.text


def test_history_pagination_preserves_filters(history_client):
    client, fs = history_client
    for _ in range(25):
        _seed(fs, "AAA")

    r = client.get("/user/history?project=AAA&search=TST")
    assert r.status_code == 200
    assert "project=AAA" in r.text
    assert "search=TST" in r.text
