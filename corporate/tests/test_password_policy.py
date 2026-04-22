"""
Tests for the password policy (corporate side).

Policy: min 12 chars, with uppercase, lowercase, digit, and symbol.
Applied on create_user, create_admin_user, and update_user_password.
"""

import pytest


@pytest.fixture(autouse=True)
def isolate_users(tmp_path, monkeypatch):
    """Point the users file at a fresh temp location for every test."""
    from app import auth

    monkeypatch.setattr(auth, "USERS_FILE_PATH", tmp_path / "users.json")
    yield


# -------------------- validator helper --------------------


@pytest.mark.parametrize(
    "password",
    [
        "",
        "short",
        "onlyletters",  # no digit, no upper, no symbol
        "Password1",  # only 9 chars
        "Password12!",  # 11 chars, just under min
        "alllowercase1!",
        "ALLUPPERCASE1!",
        "NoDigitsHere!!!",
        "NoSymbolsHere12",
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
        "GoodPass123!",  # exactly 12, one of each
        "LongerPassword9$",  # longer, mixed
        "AnotherValid1@abc",  # 17 chars
    ],
)
def test_validate_accepts_strong(password):
    from app import auth

    ok, msg = auth.validate_password_strength(password)
    assert ok is True
    assert msg == ""


def test_validator_error_message_lists_what_is_missing():
    from app import auth

    ok, msg = auth.validate_password_strength("nocapitals1!")
    assert ok is False
    assert "uppercase" in msg.lower()


# -------------------- applied on create_user --------------------


def test_create_user_rejects_weak_password():
    from app import auth

    ok, msg = auth.create_user("alice", "weak")
    assert ok is False
    assert "12" in msg or "uppercase" in msg.lower() or "lowercase" in msg.lower()


def test_create_user_accepts_strong_password():
    from app import auth

    ok, msg = auth.create_user("alice", "StrongPass1!")
    assert ok is True


# -------------------- applied on update_user_password --------------------


def test_update_password_rejects_weak():
    from app import auth

    auth.create_user("bob", "StrongPass1!")
    ok, msg = auth.update_user_password("bob", "short")
    assert ok is False


def test_update_password_accepts_strong():
    from app import auth

    auth.create_user("bob", "StrongPass1!")
    ok, msg = auth.update_user_password("bob", "NewPass9876#")
    assert ok is True


# -------------------- applied on create_admin_user --------------------


def test_create_admin_rejects_weak():
    from app import auth

    ok, msg = auth.create_admin_user("newadmin", "weak")
    assert ok is False


def test_create_admin_accepts_strong():
    from app import auth

    ok, msg = auth.create_admin_user("newadmin", "StrongAdm1!")
    # 11 chars — should fail on length
    # Let's check the accurate one:
    ok, msg = auth.create_admin_user("newadmin2", "StrongAdm1!x")
    assert ok is True
