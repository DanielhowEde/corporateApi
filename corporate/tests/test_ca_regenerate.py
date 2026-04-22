"""
Tests for CA regeneration — admin flow + key_manager internals.

Covers:
  - get_ca_info returns correct subject fields
  - regenerate_ca archives the old CA and builds a new one with the
    supplied subject
  - All existing client certs are marked 'orphaned'
  - Admin form requires `confirm=yes`
  - Admin form rejects bad country codes
  - Admin form rejects empty common_name (HTTP 422)
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    """Isolated corporate app with a logged-in admin + fresh keys dir."""
    from app import auth, main
    from app.key_manager import KeyManager

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    auth._save_users(
        {
            "rootadmin": {
                "password_hash": auth._hash_password("AdminPass1!abc"),
                "role": "admin",
                "enabled": True,
                "must_change_password": False,
            },
        }
    )

    # Redirect the key manager at a per-test keys directory
    keys_dir = tmp_path / "keys"
    import app.admin as admin_module

    admin_module.key_manager = KeyManager(keys_dir=str(keys_dir))

    client = TestClient(main.app)
    token = auth.create_admin_session("rootadmin")
    client.cookies.set("admin_session", token)
    return client, admin_module.key_manager


# -------------------- key_manager internals --------------------


def test_get_ca_info_returns_none_before_first_use(admin_env):
    _, km = admin_env
    assert km.get_ca_info() is None


def test_get_ca_info_after_ensure(admin_env):
    _, km = admin_env
    km._ensure_ca()

    info = km.get_ca_info()
    assert info is not None
    assert info["common_name"] == "DMZ-API Internal CA"
    assert info["organization"] == "DMZ-API"
    assert info["fingerprint_sha256"]
    assert info["serial_number"]


def test_regenerate_ca_archives_old_and_builds_new(admin_env):
    _, km = admin_env

    # Generate CA + one client cert
    km._ensure_ca()
    first = km.generate_key_pair("alice", 2048)
    first_info = km.get_ca_info()

    # Regenerate with a new subject
    result = km.regenerate_ca(
        subject_fields={
            "common_name": "New Corp CA",
            "organization": "Acme Corp",
            "country": "GB",
        },
        validity_days=365,
        key_size=2048,
    )

    # Archive path is returned and exists
    assert result["archived_path"]
    from pathlib import Path

    assert Path(result["archived_path"]).exists()

    # 1 existing key marked orphaned
    assert result["orphaned_keys"] == 1

    # New CA has the new subject
    new_info = km.get_ca_info()
    assert new_info["common_name"] == "New Corp CA"
    assert new_info["organization"] == "Acme Corp"
    assert new_info["country"] == "GB"
    assert new_info["fingerprint_sha256"] != first_info["fingerprint_sha256"]

    # The client cert metadata flipped to orphaned
    reloaded = km.get_key(first["key_id"])
    assert reloaded["status"] == "orphaned"
    assert "orphaned_at" in reloaded


def test_regenerate_ca_rejects_empty_common_name(admin_env):
    from app.key_manager import KeyManagerError

    _, km = admin_env
    with pytest.raises(KeyManagerError):
        km.regenerate_ca(subject_fields={"common_name": ""}, validity_days=365, key_size=2048)


def test_regenerate_ca_rejects_invalid_validity(admin_env):
    from app.key_manager import KeyManagerError

    _, km = admin_env
    with pytest.raises(KeyManagerError):
        km.regenerate_ca(subject_fields={"common_name": "CA"}, validity_days=0, key_size=2048)


def test_mark_all_keys_orphaned_is_idempotent(admin_env):
    _, km = admin_env
    km._ensure_ca()
    km.generate_key_pair("alice", 2048)

    first = km.mark_all_keys_orphaned()
    second = km.mark_all_keys_orphaned()

    assert first == 1
    assert second == 0  # already orphaned, don't mark again


# -------------------- admin routes --------------------


def test_admin_ca_regenerate_form_renders(admin_env):
    client, km = admin_env
    km._ensure_ca()

    r = client.get("/admin/ca/regenerate")
    assert r.status_code == 200
    assert "Regenerate" in r.text
    assert "DMZ-API Internal CA" in r.text  # prefill shows current


def test_admin_ca_regenerate_requires_confirm(admin_env):
    client, km = admin_env
    km._ensure_ca()

    # No confirm checkbox ticked — must reject
    r = client.post(
        "/admin/ca/regenerate",
        data={"common_name": "Whatever", "validity_days": 365, "key_size": 2048},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers.get("location", "")
    assert "/admin/ca/regenerate" in loc and "error=" in loc


def test_admin_ca_regenerate_rejects_bad_country(admin_env):
    client, _ = admin_env

    r = client.post(
        "/admin/ca/regenerate",
        data={
            "common_name": "New CA",
            "country": "GBR",  # 3 letters — invalid
            "validity_days": 365,
            "key_size": 2048,
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Country+must+be" in r.headers.get("location", "")


def test_admin_ca_regenerate_success(admin_env):
    client, km = admin_env
    km._ensure_ca()
    km.generate_key_pair("alice", 2048)
    old_fp = km.get_ca_info()["fingerprint_sha256"]

    r = client.post(
        "/admin/ca/regenerate",
        data={
            "common_name": "Regenerated CA",
            "organization": "Acme",
            "country": "GB",
            "validity_days": 365,
            "key_size": 2048,
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/admin/keys" in r.headers.get("location", "")
    assert "message=" in r.headers.get("location", "")

    # CA subject + fingerprint have actually changed
    new_info = km.get_ca_info()
    assert new_info["common_name"] == "Regenerated CA"
    assert new_info["fingerprint_sha256"] != old_fp

    # Previous client cert is now orphaned
    assert km.list_keys()[0]["status"] == "orphaned"


def test_admin_ca_regenerate_requires_auth(admin_env):
    client, _ = admin_env
    client.cookies.clear()

    r = client.get("/admin/ca/regenerate", follow_redirects=False)
    assert r.status_code == 303
    assert "/admin/login" in r.headers.get("location", "")
