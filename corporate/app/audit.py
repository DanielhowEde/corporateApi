"""
Audit log for failed authentication attempts (corporate = central sink).

Every failed login — from corporate admin, corporate user, or the low-side
(forwarded via the gateway) — lands here. Events are:

  1. Written to the Python logger at WARNING
  2. Appended to a daily JSONL file under {data_dir}/audit/failed_logins/

The JSONL format makes the audit trail easy to tail, grep, or ship to a
SIEM. Each event is a single line of JSON:

  {
    "timestamp": "2026-04-17T09:22:33",
    "source":    "corporate-admin" | "corporate-user" | "low-side",
    "username":  "attempted-username",
    "reason":    "bad_password" | "user_disabled" | "user_not_found" | ...,
    "request_id": "uuid"
  }
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import config
from .utils import get_request_id, setup_logging

logger = setup_logging("audit")

_VALID_SOURCES = {"corporate-admin", "corporate-user", "low-side"}


def _audit_dir() -> Path:
    """Failed-login events live under {data_dir}/audit/failed_logins/."""
    path = config.master_dir.parent / "audit" / "failed_logins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _today_file() -> Path:
    return _audit_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def record_failed_login(
    username: str,
    source: str,
    reason: str = "bad_password",
    request_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record a failed login attempt.

    Args:
        username:   Username that was attempted (may not exist)
        source:     One of VALID_SOURCES — where the attempt happened
        reason:     Machine-friendly reason code
        request_id: Correlation id (defaults to current request context)
        timestamp:  ISO 8601 (defaults to now) — supplied when replaying
                    events forwarded from the low-side

    Returns:
        The event dict that was persisted.
    """
    if source not in _VALID_SOURCES:
        logger.warning(f"Unknown audit source '{source}', treating as 'unknown'")
        source = "unknown"

    event = {
        "timestamp": timestamp or datetime.now().isoformat(),
        "source": source,
        "username": (username or "").strip(),
        "reason": reason,
        "request_id": request_id or get_request_id(),
    }

    # Always log. Failing the append is non-fatal — we don't want to let
    # disk issues block the login flow.
    logger.warning(
        f"FAILED LOGIN source={event['source']} username={event['username']!r} "
        f"reason={event['reason']} request_id={event['request_id']}"
    )

    try:
        with open(_today_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.error(f"Failed to append audit record: {e}")

    return event


def list_recent_failed_logins(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Return the most recent failed-login events across today + the prior 6
    days, newest first. Capped at `limit` entries.
    """
    events: List[Dict[str, Any]] = []
    audit_dir = _audit_dir()
    if not audit_dir.exists():
        return events

    # Read the N most recent daily files (sorted descending).
    files = sorted(
        (p for p in audit_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"),
        reverse=True,
    )
    for path in files[:14]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
        if len(events) >= limit * 2:  # rough over-fetch; we sort + trim below
            break

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:limit]
