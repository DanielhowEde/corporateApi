"""
Integration test suite executed by the GitLab CI `integration` stage.

Assumes the following services are running on localhost:
  :8003  Mock cert gateway
  :8000  Mock DMZ gateway
  :8001  Corporate API
  :8002  Low-side API
  Whitelist seeded with projects AAA and BBB.

Exits non-zero on any failure so CI fails cleanly, but continues past a
failed test so the full report shows every break at once.
"""

import sys
import uuid
from datetime import UTC, datetime

import httpx

CERT_GW = "http://localhost:8003"
GATEWAY = "http://localhost:8000"
CORPORATE = "http://localhost:8001"
LOW_SIDE = "http://localhost:8002"


def _new_msg(project: str = "AAA", test_id: str = "TST000") -> dict:
    return {
        "ID": str(uuid.uuid4()),
        "Project": project,
        "TestID": test_id,
        "Area": "Integration",
        "Date": "2026-04-08T12:00:00",
        "Status": "Inprogress",
        "Data": {"source": "ci", "test": test_id},
    }


def test_full_message_flow() -> None:
    """Corporate /messages → cert wrap → gateway → both sides."""
    msg = _new_msg(test_id="TST001")
    print(f"[1] Full flow: sending {msg['ID']}")

    r = httpx.post(f"{CORPORATE}/messages", json=msg, timeout=30)
    print(f"    corporate response: {r.status_code} {r.text[:200]}")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body["success"] is True, f"success=false: {body}"
    assert body["message_id"] == msg["ID"], "message_id mismatch"


def test_cert_wrap() -> None:
    """Direct call to cert gateway returns a valid envelope."""
    msg = _new_msg(test_id="TST002")
    print(f"[2] Cert wrap: {msg['ID']}")

    r = httpx.post(f"{CERT_GW}/wrap", json=msg, timeout=10)
    assert r.status_code == 200, f"cert gw error: {r.status_code}"

    body = r.json()
    for field in ("token", "expires_at", "message"):
        assert field in body, f"missing field '{field}' in wrapped response"
    print(f"    token={body['token'][:40]}... expires={body['expires_at']}")


def test_low_side_receive() -> None:
    """Direct POST to low-side /dmz/messages stores the message."""
    msg = _new_msg(test_id="TST003")
    print(f"[3] Low-side receive: {msg['ID']}")

    r = httpx.post(f"{LOW_SIDE}/dmz/messages", json=msg, timeout=10)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"


def test_invalid_project_rejected() -> None:
    """Corporate rejects messages for projects not in the whitelist."""
    msg = _new_msg(project="ZZZ", test_id="TST004")
    print(f"[4] Non-whitelisted project: {msg['ID']}")

    r = httpx.post(f"{CORPORATE}/messages", json=msg, timeout=10)
    assert r.status_code == 400, f"expected 400, got {r.status_code}"


# =============================================================================
# New cross-service flows added since the initial suite
# =============================================================================


def test_audit_failed_login_forwarded_to_corporate() -> None:
    """
    Failed-login audit event flows: low-side → gateway /audit/failed-login
    → corporate /dmz/audit/failed-login. Corporate persists it to the audit log.
    """
    print("[5] Audit forwarding: low-side → gateway → corporate")
    payload = {
        "username": f"itester-{uuid.uuid4().hex[:6]}",
        "reason": "bad_password",
        "source": "low-side",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    r = httpx.post(f"{GATEWAY}/audit/failed-login", json=payload, timeout=10)
    assert r.status_code == 200, f"gateway returned {r.status_code}: {r.text}"

    # Corporate should also accept the event directly from the gateway.
    # This is what the gateway forwards to.
    r2 = httpx.post(f"{CORPORATE}/dmz/audit/failed-login", json=payload, timeout=10)
    assert r2.status_code == 200, f"corporate returned {r2.status_code}: {r2.text}"


def test_low_side_login_failure_returns_redirect() -> None:
    """
    End-to-end audit side effect: a failed low-side login must return 303
    (exercising the path that asynchronously forwards to corporate).
    We can only verify the HTTP response here — persistence is verified
    by the unit tests.
    """
    print("[6] Low-side login failure triggers audit forward")
    r = httpx.post(
        f"{LOW_SIDE}/user/login",
        data={"username": "nonexistent", "password": "wrong"},
        follow_redirects=False,
        timeout=10,
    )
    assert r.status_code == 303, f"expected 303 redirect, got {r.status_code}"
    assert "error=" in r.headers.get("location", ""), "expected error query param"


def test_user_sync_with_allowed_projects() -> None:
    """
    User sync with per-user project grants: corporate → gateway /users
    → low-side /dmz/users. Payload must round-trip the allowed_projects list.
    """
    print("[7] User sync with allowed_projects")
    payload = {
        "username": f"itester-{uuid.uuid4().hex[:6]}",
        "action": "upsert",
        "password_hash": "sha256$irrelevant",
        "enabled": True,
        "must_change_password": False,
        "allowed_projects": ["AAA", "BBB"],
    }
    r = httpx.post(f"{GATEWAY}/users", json=payload, timeout=10)
    assert r.status_code == 200, f"gateway returned {r.status_code}: {r.text}"

    # Same payload direct to low-side /dmz/users should succeed
    r2 = httpx.post(f"{LOW_SIDE}/dmz/users", json=payload, timeout=10)
    assert r2.status_code == 200, f"low-side returned {r2.status_code}: {r2.text}"


def test_ca_sync_flow() -> None:
    """
    CA certificate sync: corporate → gateway /ca → low-side /dmz/ca.
    Low-side uses this CA as the trust root for client certs corporate issues.
    """
    print("[8] CA certificate sync")
    fake_ca_pem = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIBfakeCAcertforintegrationtestingonly==\n"
        "-----END CERTIFICATE-----\n"
    )
    payload = {"ca_pem": fake_ca_pem}

    r = httpx.post(f"{GATEWAY}/ca", json=payload, timeout=10)
    assert r.status_code == 200, f"gateway returned {r.status_code}: {r.text}"

    r2 = httpx.post(f"{LOW_SIDE}/dmz/ca", json=payload, timeout=10)
    assert r2.status_code == 200, f"low-side returned {r2.status_code}: {r2.text}"


def test_client_cert_sync_flow() -> None:
    """
    Client certificate sync: corporate → gateway /client-certs → low-side
    /dmz/client-certs. Low-side records an allowlist entry (public cert only).
    """
    print("[9] Client cert sync (public key only)")
    fake_cert_pem = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIBfakeCLIENTcertforintegrationtestingonly==\n"
        "-----END CERTIFICATE-----\n"
    )
    payload = {
        "key_id": str(uuid.uuid4()),
        "name": "itester-cert",
        "cert_pem": fake_cert_pem,
        "action": "upsert",
    }

    r = httpx.post(f"{GATEWAY}/client-certs", json=payload, timeout=10)
    assert r.status_code == 200, f"gateway returned {r.status_code}: {r.text}"

    r2 = httpx.post(f"{LOW_SIDE}/dmz/client-certs", json=payload, timeout=10)
    assert r2.status_code == 200, f"low-side returned {r2.status_code}: {r2.text}"


def test_mock_gateway_unwraps_jwt_envelope() -> None:
    """
    The mock gateway must unwrap {token, expires_at, message} envelopes
    before forwarding to downstream services — otherwise low-side rejects
    the payload as schema-invalid.
    """
    print("[10] Mock gateway JWT unwrap")
    inner = _new_msg(test_id="TST010")
    envelope = {
        "token": "eyJhbGciOiJSUzI1NiJ9.fake.sig",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "message": inner,
    }
    r = httpx.post(f"{GATEWAY}/messages", json=envelope, timeout=15)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"


def main() -> int:
    tests = [
        test_full_message_flow,
        test_cert_wrap,
        test_low_side_receive,
        test_invalid_project_rejected,
        test_audit_failed_login_forwarded_to_corporate,
        test_low_side_login_failure_returns_redirect,
        test_user_sync_with_allowed_projects,
        test_ca_sync_flow,
        test_client_cert_sync_flow,
        test_mock_gateway_unwraps_jwt_envelope,
    ]
    failures: list[tuple[str, BaseException]] = []

    for fn in tests:
        try:
            fn()
        except (AssertionError, httpx.HTTPError) as e:
            print(f"    FAIL {fn.__name__}: {e}")
            failures.append((fn.__name__, e))

    print()
    if failures:
        print(f"{len(failures)} integration test(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print(f"All {len(tests)} integration tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
