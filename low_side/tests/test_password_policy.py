"""
Tests for the password policy (low-side).

Low-side only updates passwords via the change-password flow; user
creation happens via sync from corporate where the hash is already set.
"""

import pytest


@pytest.fixture(autouse=True)
def isolate_users(tmp_path, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    yield


@pytest.mark.parametrize(
    "password",
    [
        "",
        "short",
        "Password1",  # 9 chars
        "nocapitals12!",
        "NOLOWER123!",
        "NoDigitsABC!",
        "NoSymbol1234A",
    ],
)
def test_validate_rejects_weak(password):
    from app import auth

    ok, msg = auth.validate_password_strength(password)
    assert ok is False
    assert msg


@pytest.mark.parametrize(
    "password",
    [
        "GoodPass123!",
        "LongerPassword9$",
        "AnotherValid1@abc",
    ],
)
def test_validate_accepts_strong(password):
    from app import auth

    ok, msg = auth.validate_password_strength(password)
    assert ok is True
    assert msg == ""


def test_update_password_rejects_weak():
    from app import auth

    auth._save_users(
        {
            "alice": {
                "password_hash": auth._hash_password("StrongOld1!"),
                "enabled": True,
                "must_change_password": False,
            }
        }
    )
    ok, msg = auth.update_user_password("alice", "short")
    assert ok is False


def test_update_password_accepts_strong():
    from app import auth

    auth._save_users(
        {
            "alice": {
                "password_hash": auth._hash_password("StrongOld1!"),
                "enabled": True,
                "must_change_password": False,
            }
        }
    )
    ok, msg = auth.update_user_password("alice", "StrongNew9@xyz")
    assert ok is True
