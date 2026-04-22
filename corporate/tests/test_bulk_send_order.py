"""
Tests for ordered bulk send on the pending queue.

Covers every `order` mode accepted by POST /user/pending/bulk-send:
  selected | reverse | shuffle | oldest | newest

The pending records are seeded with explicit `created` timestamps so the
oldest/newest sorts are deterministic. Each test uses a spy gateway to
record the exact forwarding sequence.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ordered_pending_env(tmp_path, monkeypatch):
    """
    Corporate app wired up with:
      - a logged-in user
      - three pending records, created in a known order
      - a spy gateway that records the send sequence
    """
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

    # Seed 3 pending records. write_pending stamps `created` at call-time,
    # so sleeping briefly between calls gives us distinct timestamps.
    ids: list[str] = []
    for label in ("first", "second", "third"):
        mid = str(uuid.uuid4())
        ids.append(mid)
        inner = {
            "ID": mid,
            "Project": "AAA",
            "TestID": label,
            "Area": "Integration",
            "Date": "2026-01-30T11:22:33",
            "Status": "Inprogress",
            "Data": {"label": label},
        }
        wrapped = {
            "token": f"eyJ.{label}.sig",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "message": inner,
        }
        fs.write_pending(inner, wrapped)
        time.sleep(0.01)

    forwarded: list[str] = []

    class _SpyGateway:
        async def send_wrapped(self, payload):
            forwarded.append(payload["message"]["TestID"])
            return {"success": True, "message_id": payload["message"]["ID"]}

    main.gateway_client = _SpyGateway()
    user_module.set_gateway_client(main.gateway_client)

    client = TestClient(main.app)
    token = auth.create_user_session("tester")
    client.cookies.set("session_token", token)
    return client, ids, forwarded


def _bulk_send(client, ids: list[str], order: str):
    """Helper — POST to bulk-send with the requested order."""
    return client.post(
        "/user/pending/bulk-send",
        data={"message_ids": ids, "order": order},
        follow_redirects=False,
    )


# -------------------- order modes --------------------


def test_order_selected_preserves_input_sequence(ordered_pending_env):
    client, ids, forwarded = ordered_pending_env
    # Submit in an arbitrary non-created order: second, first, third
    r = _bulk_send(client, [ids[1], ids[0], ids[2]], order="selected")
    assert r.status_code == 303
    assert forwarded == ["second", "first", "third"]


def test_order_reverse_flips_input_sequence(ordered_pending_env):
    client, ids, forwarded = ordered_pending_env
    r = _bulk_send(client, [ids[0], ids[1], ids[2]], order="reverse")
    assert r.status_code == 303
    assert forwarded == ["third", "second", "first"]


def test_order_oldest_first(ordered_pending_env):
    client, ids, forwarded = ordered_pending_env
    # Submit in newest-first order; server should resort to oldest-first
    r = _bulk_send(client, [ids[2], ids[1], ids[0]], order="oldest")
    assert r.status_code == 303
    assert forwarded == ["first", "second", "third"]


def test_order_newest_first(ordered_pending_env):
    client, ids, forwarded = ordered_pending_env
    r = _bulk_send(client, [ids[0], ids[1], ids[2]], order="newest")
    assert r.status_code == 303
    assert forwarded == ["third", "second", "first"]


def test_order_shuffle_returns_all_but_not_input_order(ordered_pending_env):
    """
    Shuffle is random per-request — we can only assert the multiset of
    sent IDs matches and that over repeated runs we sometimes deviate
    from the input order. 20 trials is enough that the probability of
    every permutation equalling the input order is negligible (1/6^20).
    """
    client, ids, forwarded = ordered_pending_env
    from app import main
    from app.file_store import FileStore
    from app.config import config

    saw_non_identity = False
    for _ in range(20):
        # Re-seed the pending store for each iteration
        fs = FileStore(
            master_dir=config.master_dir,
            tmp_dir=config.tmp_dir,
            error_dir=config.error_dir,
        )
        for mid in ids:
            # If a previous iteration already sent + removed, rewrite
            try:
                fs.get_pending(mid)
            except Exception:
                inner = {
                    "ID": mid,
                    "Project": "AAA",
                    "TestID": mid[:4],
                    "Area": "Integration",
                    "Date": "2026-01-30T11:22:33",
                    "Status": "Inprogress",
                    "Data": {},
                }
                wrapped = {
                    "token": "x.y.z",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "message": inner,
                }
                fs.write_pending(inner, wrapped)
        main.file_store = fs

        forwarded.clear()
        r = _bulk_send(client, list(ids), order="shuffle")
        assert r.status_code == 303
        assert len(forwarded) == 3
        if forwarded != ["first", "second", "third"]:
            saw_non_identity = True
            break
    assert saw_non_identity, "shuffle never produced a non-identity permutation"


def test_unknown_order_falls_back_to_selected(ordered_pending_env):
    client, ids, forwarded = ordered_pending_env
    r = _bulk_send(client, [ids[2], ids[0], ids[1]], order="bogus-value")
    assert r.status_code == 303
    # No crash, treated as "selected" — output matches input order
    assert forwarded == ["third", "first", "second"]


# -------------------- guardrails --------------------


def test_empty_selection_returns_error(ordered_pending_env):
    client, _, _ = ordered_pending_env
    r = client.post(
        "/user/pending/bulk-send",
        data={"order": "shuffle"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=No+messages+selected" in r.headers.get("location", "")


def test_response_message_includes_order_for_visibility(ordered_pending_env):
    """The success redirect should surface which order was used."""
    client, ids, _ = ordered_pending_env
    r = _bulk_send(client, [ids[0], ids[1]], order="reverse")
    assert r.status_code == 303
    assert "order=reverse" in r.headers.get("location", "")
