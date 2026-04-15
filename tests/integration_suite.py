"""
Integration test suite executed by the GitLab CI `integration` stage.

Assumes the following services are running on localhost:
  :8003  Mock cert gateway
  :8000  Mock DMZ gateway
  :8001  Corporate API
  :8002  Low-side API
  Whitelist seeded with projects AAA and BBB.

Exits non-zero on the first failure so CI fails cleanly.
"""
import sys
import uuid

import httpx


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

    r = httpx.post("http://localhost:8001/messages", json=msg, timeout=30)
    print(f"    corporate response: {r.status_code} {r.text[:200]}")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body["success"] is True, f"success=false: {body}"
    assert body["message_id"] == msg["ID"], "message_id mismatch"


def test_cert_wrap() -> None:
    """Direct call to cert gateway returns a valid envelope."""
    msg = _new_msg(test_id="TST002")
    print(f"[2] Cert wrap: {msg['ID']}")

    r = httpx.post("http://localhost:8003/wrap", json=msg, timeout=10)
    assert r.status_code == 200, f"cert gw error: {r.status_code}"

    body = r.json()
    for field in ("token", "expires_at", "message"):
        assert field in body, f"missing field '{field}' in wrapped response"
    print(f"    token={body['token'][:40]}... expires={body['expires_at']}")


def test_low_side_receive() -> None:
    """Direct POST to low-side /dmz/messages stores the message."""
    msg = _new_msg(test_id="TST003")
    print(f"[3] Low-side receive: {msg['ID']}")

    r = httpx.post("http://localhost:8002/dmz/messages", json=msg, timeout=10)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"


def test_invalid_project_rejected() -> None:
    """Corporate rejects messages for projects not in the whitelist."""
    msg = _new_msg(project="ZZZ", test_id="TST004")
    print(f"[4] Non-whitelisted project: {msg['ID']}")

    r = httpx.post("http://localhost:8001/messages", json=msg, timeout=10)
    assert r.status_code == 400, f"expected 400, got {r.status_code}"


def main() -> int:
    tests = [
        test_full_message_flow,
        test_cert_wrap,
        test_low_side_receive,
        test_invalid_project_rejected,
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
