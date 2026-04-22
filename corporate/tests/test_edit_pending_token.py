"""
Tests for editing the JWT token on a pending message.

Covers:
  - file_store.update_pending_token persists changes + edit history
  - GET /user/pending/{id}/edit-token renders prefilled form
  - POST /user/pending/{id}/edit-token saves new token + expiry
  - Send Now after edit forwards the modified envelope to the gateway
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def pending_client(tmp_path, monkeypatch):
    """
    Corporate app with:
      - temp dirs for pending + file store
      - a logged-in user
      - a pre-populated pending message with a known token/expiry
      - a spy gateway client so we can assert what got forwarded on Send Now
    """
    from app import auth, main
    from app import user as user_module
    from app.config import config
    from app.file_store import FileStore
    from app.whitelist import ProjectWhitelist

    # Isolate users + pending dir
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
    wl = ProjectWhitelist(file_path=str(tmp_path / "whitelist.json"))
    wl.add_project("AAA")
    main.file_store = fs
    main.whitelist = wl
    user_module.set_file_store(fs)
    user_module.set_whitelist(wl)

    # Seed a pending message
    message_id = str(uuid.uuid4())
    inner = {
        "ID": message_id,
        "Project": "AAA",
        "TestID": "TST001",
        "Area": "Integration",
        "Date": "2026-01-30T11:22:33",
        "Status": "Inprogress",
        "Data": {"result": "pass"},
    }
    wrapped = {
        "token": "eyJhbGciOiJSUzI1NiJ9.ORIGINAL.signature",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "message": inner,
    }
    fs.write_pending(inner, wrapped)

    # Spy gateway that records the payload it was asked to forward
    forwarded: list[dict] = []

    class _SpyGateway:
        async def send_wrapped(self, payload):
            forwarded.append(payload)
            return {"success": True, "message_id": message_id}

    main.gateway_client = _SpyGateway()
    user_module.set_gateway_client(main.gateway_client)

    client = TestClient(main.app)
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)
    return client, fs, message_id, forwarded


# -------------------- file_store.update_pending_token --------------------


def test_update_pending_token_replaces_token(pending_client):
    _, fs, message_id, _ = pending_client

    record = fs.update_pending_token(message_id, token="TOTALLY.NEW.TOKEN")
    assert record["wrapped"]["token"] == "TOTALLY.NEW.TOKEN"
    # expires_at preserved when not supplied
    assert record["wrapped"]["expires_at"] == "2099-01-01T00:00:00+00:00"


def test_update_pending_token_replaces_expiry(pending_client):
    _, fs, message_id, _ = pending_client

    record = fs.update_pending_token(
        message_id,
        token="x.y.z",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert record["wrapped"]["expires_at"] == "2000-01-01T00:00:00+00:00"


def test_update_pending_token_records_history(pending_client):
    _, fs, message_id, _ = pending_client

    fs.update_pending_token(message_id, token="first.edit.token")
    fs.update_pending_token(message_id, token="second.edit.token")

    record = fs.get_pending(message_id)
    history = record.get("token_edits", [])
    assert len(history) == 2
    assert history[0]["token_prefix"].startswith("first")
    assert history[1]["token_prefix"].startswith("second")


def test_update_pending_token_missing_id_raises(pending_client):
    from app.file_store import FileStoreError

    _, fs, _, _ = pending_client
    with pytest.raises(FileStoreError):
        fs.update_pending_token("no-such-id", token="whatever")


# -------------------- route tests --------------------


def test_edit_token_form_renders_current_values(pending_client):
    client, _, message_id, _ = pending_client

    r = client.get(f"/user/pending/{message_id}/edit-token")
    assert r.status_code == 200
    assert "eyJhbGciOiJSUzI1NiJ9.ORIGINAL.signature" in r.text
    assert "2099-01-01T00:00:00+00:00" in r.text


def test_edit_token_form_404s_for_unknown_id(pending_client):
    client, _, _, _ = pending_client

    r = client.get(
        "/user/pending/00000000-0000-0000-0000-000000000000/edit-token",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/user/pending" in r.headers.get("location", "")
    assert "error=" in r.headers.get("location", "")


def test_edit_token_submit_updates_record(pending_client):
    client, fs, message_id, _ = pending_client

    r = client.post(
        f"/user/pending/{message_id}/edit-token",
        data={
            "token": "NEW.TEST.TOKEN",
            "expires_at": "2000-01-01T00:00:00+00:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/user/pending" in r.headers.get("location", "")
    assert "message=" in r.headers.get("location", "")

    record = fs.get_pending(message_id)
    assert record["wrapped"]["token"] == "NEW.TEST.TOKEN"
    assert record["wrapped"]["expires_at"] == "2000-01-01T00:00:00+00:00"


def test_edit_token_submit_requires_auth(pending_client):
    client, _, message_id, _ = pending_client
    client.cookies.clear()

    r = client.post(
        f"/user/pending/{message_id}/edit-token",
        data={"token": "x.y.z"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/user/login" in r.headers.get("location", "")


# -------------------- end-to-end: edit then send --------------------


def test_send_after_edit_forwards_modified_envelope(pending_client):
    """
    The whole point of this feature: edit the token, then Send Now, and
    verify the modified envelope is what the gateway received.
    """
    client, _fs, message_id, forwarded = pending_client

    # Edit the token to something obviously wrong
    client.post(
        f"/user/pending/{message_id}/edit-token",
        data={
            "token": "INVALID.JWT.FORCORPTESTING",
            "expires_at": "1999-12-31T23:59:59+00:00",
        },
    )

    # Now hit Send Now
    r = client.post(f"/user/pending/{message_id}/send", follow_redirects=False)
    assert r.status_code == 303

    # The spy gateway should have received the envelope as modified
    assert len(forwarded) == 1
    envelope = forwarded[0]
    assert envelope["token"] == "INVALID.JWT.FORCORPTESTING"
    assert envelope["expires_at"] == "1999-12-31T23:59:59+00:00"
    # Inner message must still be intact
    assert envelope["message"]["ID"] == message_id
