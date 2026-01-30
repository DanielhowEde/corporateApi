"""
Authentication and user management for Corporate DMZ API.

Provides:
- Admin authentication (password-based)
- User account management (admin creates users)
- Session management for both admin and users
"""
import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import setup_logging

logger = setup_logging("auth")


# Configuration from environment
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
USERS_FILE_PATH = Path(os.environ.get("USERS_FILE_PATH", "./data/users.json"))
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))

# Active sessions: token -> {"type": "admin"|"user", "username": str, "expiry": float}
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
    if not USERS_FILE_PATH.exists():
        return {}
    try:
        with open(USERS_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("users", {})
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load users file: {e}")
        return {}


def _save_users(users: Dict[str, dict]) -> bool:
    """Save users to file atomically."""
    USERS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USERS_FILE_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.rename(USERS_FILE_PATH)
        return True
    except OSError as e:
        logger.error(f"Failed to save users file: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False


# =============================================================================
# Admin Authentication
# =============================================================================

def verify_admin_password(password: str) -> bool:
    """Verify admin password."""
    return password == ADMIN_PASSWORD


def create_admin_session() -> str:
    """Create a new admin session."""
    token = secrets.token_urlsafe(32)
    expiry = datetime.now().timestamp() + (8 * 60 * 60)  # 8 hours
    active_sessions[token] = {
        "type": "admin",
        "username": "admin",
        "expiry": expiry
    }
    logger.info("Admin session created")
    return token


def verify_admin_session(token: Optional[str]) -> bool:
    """Verify if a session token is a valid admin session."""
    if not token or token not in active_sessions:
        return False
    session = active_sessions[token]
    if session["type"] != "admin":
        return False
    if datetime.now().timestamp() > session["expiry"]:
        del active_sessions[token]
        return False
    return True


# =============================================================================
# User Authentication
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
    active_sessions[token] = {
        "type": "user",
        "username": username,
        "expiry": expiry
    }
    logger.info(f"User session created: {username}")
    return token


def verify_user_session(token: Optional[str]) -> Optional[str]:
    """
    Verify if a session token is a valid user session.
    Returns the username if valid, None otherwise.
    """
    if not token or token not in active_sessions:
        return None
    session = active_sessions[token]
    if session["type"] != "user":
        return None
    if datetime.now().timestamp() > session["expiry"]:
        del active_sessions[token]
        return None
    return session["username"]


def invalidate_session(token: str) -> None:
    """Invalidate any session token."""
    if token in active_sessions:
        del active_sessions[token]


# =============================================================================
# User Management (Admin Functions)
# =============================================================================

def create_user(username: str, password: str, enabled: bool = True, must_change_password: bool = True) -> Tuple[bool, str]:
    """
    Create a new user account.
    Returns (success, message).
    """
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters"

    users = _load_users()
    if username in users:
        return False, f"User '{username}' already exists"

    users[username] = {
        "password_hash": _hash_password(password),
        "enabled": enabled,
        "must_change_password": must_change_password,
        "created": datetime.now().isoformat()
    }

    if _save_users(users):
        logger.info(f"User created: {username}")
        return True, f"User '{username}' created successfully"
    return False, "Failed to save user"


def update_user_password(username: str, new_password: str, clear_must_change: bool = True) -> Tuple[bool, str]:
    """Update a user's password."""
    if not new_password or len(new_password) < 6:
        return False, "Password must be at least 6 characters"

    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    users[username]["password_hash"] = _hash_password(new_password)
    users[username]["updated"] = datetime.now().isoformat()
    if clear_must_change:
        users[username]["must_change_password"] = False

    if _save_users(users):
        logger.info(f"Password updated for user: {username}")
        return True, f"Password updated for '{username}'"
    return False, "Failed to save changes"


def user_must_change_password(username: str) -> bool:
    """Check if user must change their password on login."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    return user.get("must_change_password", False)


def enable_user(username: str) -> Tuple[bool, str]:
    """Enable a user account."""
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    users[username]["enabled"] = True
    if _save_users(users):
        logger.info(f"User enabled: {username}")
        return True, f"User '{username}' enabled"
    return False, "Failed to save changes"


def disable_user(username: str) -> Tuple[bool, str]:
    """Disable a user account."""
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    users[username]["enabled"] = False
    if _save_users(users):
        logger.info(f"User disabled: {username}")
        return True, f"User '{username}' disabled"
    return False, "Failed to save changes"


def delete_user(username: str) -> Tuple[bool, str]:
    """Delete a user account."""
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    del users[username]
    if _save_users(users):
        logger.info(f"User deleted: {username}")
        return True, f"User '{username}' deleted"
    return False, "Failed to save changes"


def list_users() -> List[Tuple[str, bool, str]]:
    """
    List all users.
    Returns list of (username, enabled, created_date).
    """
    users = _load_users()
    return [
        (username, user.get("enabled", True), user.get("created", ""))
        for username, user in sorted(users.items())
    ]


def get_user_count() -> int:
    """Get the number of users."""
    return len(_load_users())
