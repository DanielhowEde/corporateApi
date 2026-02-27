"""
Authentication for Low-Side DMZ API.

User accounts are synced from corporate admin via the DMZ Gateway.
The /dmz/users endpoint receives user data and stores it here.

Supports: login, session management, password change.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .config import config
from .utils import setup_logging

logger = setup_logging("low_side_auth")

USERS_FILE_PATH = config.users_file_path

# Active sessions: token -> {username, expiry}
active_sessions: Dict[str, dict] = {}


def _hash_password(password: str, salt: str = "") -> str:
    """Hash a password with optional salt."""
    if not salt:
        salt = secrets.token_hex(16)
    combined = f"{salt}:{password}"
    hashed = hashlib.sha256(combined.encode()).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash."""
    try:
        salt, _ = stored_hash.split(":", 1)
        return _hash_password(password, salt) == stored_hash
    except ValueError:
        return False


def _load_users() -> Dict[str, dict]:
    """Load users from file."""
    path = config.users_file_path
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("users", {})
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load users file: {e}")
        return {}


def _save_users(users: Dict[str, dict]) -> bool:
    """Save users to file atomically."""
    path = config.users_file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        logger.error(f"Failed to save users file: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False


# =============================================================================
# User Sync (called from /dmz/users endpoint)
# =============================================================================

def sync_user_from_corporate(user_data: dict) -> tuple[bool, str]:
    """
    Apply a user sync event received from corporate via gateway.

    user_data keys:
        username       - str
        action         - "upsert" | "delete"
        password_hash  - str (already hashed, from corporate)
        enabled        - bool
        must_change_password - bool
    """
    username = user_data.get("username", "").strip()
    action = user_data.get("action", "upsert")

    if not username:
        return False, "Missing username"

    users = _load_users()

    if action == "delete":
        if username in users:
            del users[username]
            _save_users(users)
            logger.info(f"User deleted via sync: {username}")
            return True, f"User '{username}' deleted"
        return True, f"User '{username}' not found (already deleted)"

    # upsert
    users[username] = {
        "password_hash": user_data.get("password_hash", ""),
        "enabled": user_data.get("enabled", True),
        "must_change_password": user_data.get("must_change_password", True),
        "synced": datetime.now().isoformat(),
    }
    _save_users(users)
    logger.info(f"User upserted via sync: {username}")
    return True, f"User '{username}' synced"


# =============================================================================
# Authentication
# =============================================================================

def verify_user_credentials(username: str, password: str) -> bool:
    """Verify user credentials."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    if not user.get("enabled", True):
        logger.warning(f"Login attempt for disabled user: {username}")
        return False
    return _verify_password_hash(password, user.get("password_hash", ""))


def create_user_session(username: str) -> str:
    """Create a new user session."""
    token = secrets.token_urlsafe(32)
    expiry = datetime.now().timestamp() + (8 * 60 * 60)  # 8 hours
    active_sessions[token] = {"username": username, "expiry": expiry}
    logger.info(f"Session created: {username}")
    return token


def verify_user_session(token: Optional[str]) -> Optional[str]:
    """Return the username if the session token is valid, else None."""
    if not token or token not in active_sessions:
        return None
    session = active_sessions[token]
    if datetime.now().timestamp() > session["expiry"]:
        del active_sessions[token]
        return None
    return session["username"]


def invalidate_session(token: str) -> None:
    """Invalidate a session token."""
    if token in active_sessions:
        del active_sessions[token]


def user_must_change_password(username: str) -> bool:
    """Check if user must change their password on login."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    return user.get("must_change_password", False)


def update_user_password(username: str, new_password: str) -> tuple[bool, str]:
    """Update a user's password and clear the must_change_password flag."""
    if not new_password or len(new_password) < 6:
        return False, "Password must be at least 6 characters"

    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    users[username]["password_hash"] = _hash_password(new_password)
    users[username]["must_change_password"] = False
    users[username]["updated"] = datetime.now().isoformat()

    if _save_users(users):
        logger.info(f"Password updated for: {username}")
        return True, "Password updated successfully"
    return False, "Failed to save changes"
