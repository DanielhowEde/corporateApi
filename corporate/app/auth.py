"""
Authentication and user management for Corporate DMZ API.

Provides:
- Admin authentication (file-based, multi-admin)
- User account management (admin creates users)
- Session management for both admin and users

User roles:
- "admin": Can access admin panel, manage users and projects
- "user": Can access user portal, send messages
"""

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import setup_logging

logger = setup_logging("auth")


# =============================================================================
# Password policy
# =============================================================================
#
# Applied on every path that sets or resets a password: create_user,
# create_admin_user, update_user_password, and the user-driven change form.
# =============================================================================

PASSWORD_MIN_LENGTH = 12
PASSWORD_POLICY_TEXT = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters and include "
    "uppercase, lowercase, a digit, and a symbol."
)

_PASSWORD_CHECKS = [
    (re.compile(r"[A-Z]"), "an uppercase letter"),
    (re.compile(r"[a-z]"), "a lowercase letter"),
    (re.compile(r"\d"), "a digit"),
    (re.compile(r"[^A-Za-z0-9]"), "a symbol"),
]


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Enforce the password policy.

    Returns (True, "") when the password meets the policy, otherwise
    (False, reason).
    """
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    missing = [desc for pattern, desc in _PASSWORD_CHECKS if not pattern.search(password)]
    if missing:
        return False, "Password must contain " + ", ".join(missing)
    return True, ""


# Configuration from environment
_DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
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
        os.replace(tmp_path, USERS_FILE_PATH)
        return True
    except OSError as e:
        logger.error(f"Failed to save users file: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        return False


def _ensure_default_admin() -> None:
    """
    Create the default admin account from env var if no admin users exist.
    Called on module load so there is always at least one admin.
    """
    users = _load_users()
    has_admin = any(u.get("role") == "admin" for u in users.values())
    if not has_admin:
        users["admin"] = {
            "password_hash": _hash_password(_DEFAULT_ADMIN_PASSWORD),
            "role": "admin",
            "enabled": True,
            "must_change_password": True,
            "created": datetime.now().isoformat(),
        }
        _save_users(users)
        logger.info("Default admin account created")


# Initialise default admin on import
_ensure_default_admin()


# =============================================================================
# Admin Authentication
# =============================================================================


def verify_admin_credentials(username: str, password: str) -> bool:
    """Verify admin username and password."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    if user.get("role") != "admin":
        return False
    if not user.get("enabled", True):
        logger.warning(f"Login attempt for disabled admin: {username}")
        return False
    return _verify_password_hash(password, user.get("password_hash", ""))


def create_admin_session(username: str) -> str:
    """Create a new admin session."""
    token = secrets.token_urlsafe(32)
    expiry = datetime.now().timestamp() + (8 * 60 * 60)  # 8 hours
    active_sessions[token] = {"type": "admin", "username": username, "expiry": expiry}
    logger.info(f"Admin session created: {username}")
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


def get_admin_username_from_session(token: Optional[str]) -> Optional[str]:
    """Get the admin username from a valid session token."""
    if not token or token not in active_sessions:
        return None
    session = active_sessions[token]
    if session["type"] != "admin":
        return None
    if datetime.now().timestamp() > session["expiry"]:
        del active_sessions[token]
        return None
    return session["username"]


# =============================================================================
# User Authentication
# =============================================================================


def verify_user_credentials(username: str, password: str) -> bool:
    """Verify user credentials."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    if user.get("role", "user") != "user":
        return False
    if not user.get("enabled", True):
        logger.warning(f"Login attempt for disabled user: {username}")
        return False
    return _verify_password_hash(password, user.get("password_hash", ""))


def classify_login_failure(username: str, role: str) -> str:
    """
    Classify why a login failed, for audit purposes.

    Never exposed to the end user — only the generic error is shown there
    — but the specific reason is valuable in the audit log.

    Args:
        username: The username that was attempted.
        role:     Expected role ("admin" or "user").

    Returns one of:
        user_not_found | wrong_role | user_disabled | bad_password
    """
    user = _load_users().get(username)
    if not user:
        return "user_not_found"
    actual_role = user.get("role", "user")
    if actual_role != role:
        return "wrong_role"
    if not user.get("enabled", True):
        return "user_disabled"
    return "bad_password"


def create_user_session(username: str) -> str:
    """Create a new user session."""
    token = secrets.token_urlsafe(32)
    expiry = datetime.now().timestamp() + (8 * 60 * 60)  # 8 hours
    active_sessions[token] = {"type": "user", "username": username, "expiry": expiry}
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
# Admin User Management
# =============================================================================


def create_admin_user(
    username: str, password: str, enabled: bool = True
) -> Tuple[bool, str]:
    """
    Create a new admin account.
    Returns (success, message).
    """
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    ok, msg = validate_password_strength(password)
    if not ok:
        return False, msg

    users = _load_users()
    if username in users:
        return False, f"Username '{username}' already exists"

    users[username] = {
        "password_hash": _hash_password(password),
        "role": "admin",
        "enabled": enabled,
        "must_change_password": True,
        "created": datetime.now().isoformat(),
    }

    if _save_users(users):
        logger.info(f"Admin user created: {username}")
        return True, f"Admin '{username}' created successfully"
    return False, "Failed to save admin user"


def delete_admin_user(username: str) -> Tuple[bool, str]:
    """
    Delete an admin account.
    Prevents deleting the last remaining admin.
    """
    users = _load_users()
    if username not in users:
        return False, f"Admin '{username}' not found"
    if users[username].get("role") != "admin":
        return False, f"'{username}' is not an admin"

    # Count remaining admins
    admin_count = sum(1 for u in users.values() if u.get("role") == "admin")
    if admin_count <= 1:
        return False, "Cannot delete the last admin account"

    del users[username]
    if _save_users(users):
        logger.info(f"Admin user deleted: {username}")
        return True, f"Admin '{username}' deleted"
    return False, "Failed to save changes"


def list_admins() -> List[Tuple[str, bool, str]]:
    """
    List all admin users.
    Returns list of (username, enabled, created_date).
    """
    users = _load_users()
    return [
        (username, user.get("enabled", True), user.get("created", ""))
        for username, user in sorted(users.items())
        if user.get("role") == "admin"
    ]


# =============================================================================
# Regular User Management (Admin Functions)
# =============================================================================


def create_user(
    username: str,
    password: str,
    enabled: bool = True,
    must_change_password: bool = True,
) -> Tuple[bool, str]:
    """
    Create a new user account.
    Returns (success, message).
    """
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    ok, msg = validate_password_strength(password)
    if not ok:
        return False, msg

    users = _load_users()
    if username in users:
        return False, f"Username '{username}' already exists"

    users[username] = {
        "password_hash": _hash_password(password),
        "role": "user",
        "enabled": enabled,
        "must_change_password": must_change_password,
        "created": datetime.now().isoformat(),
    }

    if _save_users(users):
        logger.info(f"User created: {username}")
        return True, f"User '{username}' created successfully"
    return False, "Failed to save user"


def update_user_password(
    username: str, new_password: str, clear_must_change: bool = True
) -> Tuple[bool, str]:
    """Update a user's or admin's password."""
    ok, msg = validate_password_strength(new_password)
    if not ok:
        return False, msg

    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    users[username]["password_hash"] = _hash_password(new_password)
    users[username]["updated"] = datetime.now().isoformat()
    if clear_must_change:
        users[username]["must_change_password"] = False

    if _save_users(users):
        logger.info(f"Password updated for: {username}")
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
    """Delete a regular user account."""
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"
    if users[username].get("role") == "admin":
        return False, f"'{username}' is an admin — use delete admin instead"

    del users[username]
    if _save_users(users):
        logger.info(f"User deleted: {username}")
        return True, f"User '{username}' deleted"
    return False, "Failed to save changes"


def list_users() -> List[Tuple[str, bool, str]]:
    """
    List all regular (non-admin) users.
    Returns list of (username, enabled, created_date).
    """
    users = _load_users()
    return [
        (username, user.get("enabled", True), user.get("created", ""))
        for username, user in sorted(users.items())
        if user.get("role", "user") == "user"
    ]


def get_user_count() -> int:
    """Get the number of regular users."""
    return len([u for u in _load_users().values() if u.get("role", "user") == "user"])


# =============================================================================
# Per-user project access control
# =============================================================================


def get_user_allowed_projects(username: str) -> List[str]:
    """
    Return the list of project codes this user is allowed to send to.

    Empty list means the user has no projects assigned and cannot send
    anything (explicit-grant model). Admin assigns projects via the Users
    page.
    """
    user = _load_users().get(username, {})
    allowed = user.get("allowed_projects", [])
    if not isinstance(allowed, list):
        return []
    return [str(code).upper() for code in allowed if code]


def set_user_allowed_projects(
    username: str, project_codes: List[str]
) -> Tuple[bool, str]:
    """
    Replace a user's allowed projects list.

    Duplicates are removed, codes are uppercased, and anything blank is
    skipped. Returns (success, message).
    """
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"

    clean = sorted({str(c).upper().strip() for c in project_codes if str(c).strip()})
    users[username]["allowed_projects"] = clean
    users[username]["updated"] = datetime.now().isoformat()

    if _save_users(users):
        logger.info(f"User allowed_projects updated: {username} -> {clean}")
        return True, f"Projects for '{username}' updated"
    return False, "Failed to save user"


def user_can_send_to_project(username: str, project_code: str) -> bool:
    """True iff this user has been explicitly granted the project code."""
    return project_code.upper() in get_user_allowed_projects(username)
