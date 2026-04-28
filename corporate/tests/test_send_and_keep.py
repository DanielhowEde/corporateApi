"""
Tests for the "send & keep" pending-message flow.

Single send (POST /user/pending/{id}/send-and-keep) and bulk send with
keep=1 (POST /user/pending/bulk-send) must forward via the gateway but
leave the pending record on disk so it can be replayed.
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def keep_env(tmp_path, monkeypatch):
    """One pending record + spy gateway, user logged in."""
    from app import auth, main
    from app import user as user_module
    from app.config import config
    from app.file_store import FileStore

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    config._config["PENDING_DIR"] = str(tmp_path / "pending")

    auth._save_users(
        {
            "tester": {
                "password_hash": auth._hash_password("UserPass123!"),
                "role": "user",
                "enabled": True,
                "must_change_password": False,
                "allowed_projects": ["AAA"],
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

    mid = str(uuid.uuid4())
    inner = {
        "ID": mid,
        "Project": "AAA",
        "TestID": "TST001",
        "Area": "Integration",
        "Date": "2026-01-30T11:22:33",
        "Status": "Inprogress",
        "Data": {"k": "v"},
    }
    wrapped = {
        "token": "eyJ.tok.sig",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "message": inner,
    }
    fs.write_pending(inner, wrapped)

    forwarded: list[dict] = []

    class _SpyGateway:
        async def send_wrapped(self, payload):
            forwarded.append(payload)
            return {"success": True, "message_id": payload["message"]["ID"]}

    main.gateway_client = _SpyGateway()
    user_module.set_gateway_client(main.gateway_client)

    client = TestClient(main.app)
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)
    return client, fs, mid, forwarded


def test_send_and_keep_single_forwards_and_retains(keep_env):
    client, fs, mid, forwarded = keep_env

    r = client.post(f"/user/pending/{mid}/send-and-keep", follow_redirects=False)
    assert r.status_code == 303

    # Forwarded once
    assert len(forwarded) == 1
    assert forwarded[0]["message"]["ID"] == mid

    # Pending record still on disk — can be sent again
    assert fs.get_pending(mid) is not None


def test_send_and_keep_can_be_replayed(keep_env):
    """Hit send-and-keep twice and confirm two forwards plus retained record."""
    client, fs, mid, forwarded = keep_env

    client.post(f"/user/pending/{mid}/send-and-keep", follow_redirects=False)
    client.post(f"/user/pending/{mid}/send-and-keep", follow_redirects=False)

    assert len(forwarded) == 2
    assert fs.get_pending(mid) is not None


def test_bulk_send_with_keep_retains_records(keep_env):
    client, fs, mid, forwarded = keep_env

    r = client.post(
        "/user/pending/bulk-send",
        data={"message_ids": [mid], "order": "selected", "keep": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(forwarded) == 1
    assert fs.get_pending(mid) is not None


def test_bulk_send_without_keep_removes_records(keep_env):
    """Default behaviour (no keep flag) must still delete after send."""
    from app.file_store import FileStoreError

    client, fs, mid, forwarded = keep_env

    r = client.post(
        "/user/pending/bulk-send",
        data={"message_ids": [mid], "order": "selected"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(forwarded) == 1
    with pytest.raises(FileStoreError):
        fs.get_pending(mid)


def test_send_and_keep_missing_message_returns_error(keep_env):
    client, _, _, _ = keep_env
    bogus = str(uuid.uuid4())

    r = client.post(f"/user/pending/{bogus}/send-and-keep", follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers.get("location", "")
