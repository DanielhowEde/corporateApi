"""
Tests for the programmatic API (/api/v1) on the corporate service.

Auth is mTLS — the reverse proxy passes the client cert CN via the
`X-Client-Cert-CN` header. Each cert is issued with a project binding;
requests for any other project are rejected.
"""

import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient


def _valid_template_payload(project: str = "AAA") -> dict:
    """A complete Message payload that also satisfies the permissive schema below."""
    return {
        "ID": str(uuid.uuid4()),
        "Project": project,
        "TestID": "TST001",
        "Area": "Integration",
        "Date": "2026-01-30T11:22:33",
        "Status": "Inprogress",
        "Data": {"result": "pass", "name": "john smith"},
    }


_PERMISSIVE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["ID", "Project"],
}


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class _StubGateway:
    """Captures calls for assertion without touching any network."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, message_data, auto_send=True):
        self.sent.append(dict(message_data))
        return {"success": True, "message_id": message_data.get("ID")}


class _StubKeyManager:
    """Returns whatever project binding the test set up in advance."""

    def __init__(self, bindings: dict[str, str]):
        self.bindings = bindings

    def get_project_for_cn(self, cn: str):
        return self.bindings.get(cn)


@pytest.fixture
def api_client(tmp_path):
    """
    Configure corporate app with:
      - payloads dir rooted in tmp_path with an AAA/smoke template and
        a permissive schema
      - stubbed gateway + key manager
    Returns (client, stub_gateway, payloads_root).
    """
    from app import api, main
    from app.payload_store import PayloadStore

    payloads_root = tmp_path / "payloads"
    _write_json(payloads_root / "AAA" / "smoke.json", _valid_template_payload("AAA"))
    _write_json(payloads_root / "AAA" / "Schemas" / "base.json", _PERMISSIVE_SCHEMA)

    stub_gateway = _StubGateway()
    stub_km = _StubKeyManager({"alice": "AAA"})

    main.gateway_client = stub_gateway
    api.set_gateway_client(stub_gateway)
    api.set_key_manager(stub_km)
    api.set_payload_store(PayloadStore(payloads_dir=payloads_root))

    client = TestClient(main.app)
    return client, stub_gateway, payloads_root


def _as_alice(headers: dict | None = None) -> dict:
    return {"X-Client-Cert-CN": "alice", **(headers or {})}


# ---------------------------------------------------------------------
# Auth / project binding
# ---------------------------------------------------------------------


def test_request_without_cn_header_is_forbidden(api_client):
    client, _, _ = api_client
    r = client.post("/api/v1/messages/template/AAA/smoke", json={})
    assert r.status_code == 403


def test_cn_unbound_to_project_is_forbidden(api_client):
    """CN exists in fixture but maps to AAA, request targets BBB -> 403."""
    client, _, payloads_root = api_client
    _write_json(payloads_root / "BBB" / "smoke.json", _valid_template_payload("BBB"))
    _write_json(payloads_root / "BBB" / "Schemas" / "base.json", _PERMISSIVE_SCHEMA)

    r = client.post("/api/v1/messages/template/BBB/smoke", json={}, headers=_as_alice())
    assert r.status_code == 403


def test_dn_header_with_subject_dn_also_works(api_client):
    client, gw, _ = api_client
    r = client.post(
        "/api/v1/messages/template/AAA/smoke",
        json={},
        headers={"X-Ssl-Client-S-Dn": "CN=alice,O=DMZ-API"},
    )
    assert r.status_code == 200
    assert len(gw.sent) == 1


# ---------------------------------------------------------------------
# Template mode
# ---------------------------------------------------------------------


def test_template_mode_regenerates_id_and_date(api_client):
    client, gw, payloads_root = api_client

    template = _valid_template_payload("AAA")
    template["ID"] = "00000000-0000-0000-0000-000000000000"
    template["Date"] = "2000-01-01T00:00:00"
    _write_json(payloads_root / "AAA" / "smoke.json", template)

    r = client.post("/api/v1/messages/template/AAA/smoke", json={}, headers=_as_alice())
    assert r.status_code == 200, r.text

    sent = gw.sent[0]
    assert sent["ID"] != template["ID"]
    uuid.UUID(sent["ID"])  # must parse as a real UUID
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", sent["Date"])
    assert sent["Date"] != template["Date"]


def test_template_verbatim_preserves_id_and_date(api_client):
    client, gw, payloads_root = api_client

    template = _valid_template_payload("AAA")
    fixed_id = str(uuid.uuid4())
    template["ID"] = fixed_id
    template["Date"] = "2024-06-01T12:00:00"
    _write_json(payloads_root / "AAA" / "smoke.json", template)

    r = client.post(
        "/api/v1/messages/template/AAA/smoke",
        json={"verbatim": True},
        headers=_as_alice(),
    )
    assert r.status_code == 200, r.text
    assert gw.sent[0]["ID"] == fixed_id
    assert gw.sent[0]["Date"] == "2024-06-01T12:00:00"


def test_template_overrides_merge_into_payload(api_client):
    client, gw, _ = api_client

    r = client.post(
        "/api/v1/messages/template/AAA/smoke",
        json={"overrides": {"Area": "Regression"}},
        headers=_as_alice(),
    )
    assert r.status_code == 200, r.text
    assert gw.sent[0]["Area"] == "Regression"


def test_template_missing_returns_404(api_client):
    client, _, _ = api_client
    r = client.post("/api/v1/messages/template/AAA/nope", json={}, headers=_as_alice())
    assert r.status_code == 404


# ---------------------------------------------------------------------
# Schema mode
# ---------------------------------------------------------------------


def test_schema_mode_sends_valid_payload(api_client):
    client, gw, _ = api_client
    payload = _valid_template_payload("AAA")

    r = client.post("/api/v1/messages/schema/AAA", json=payload, headers=_as_alice())
    assert r.status_code == 200, r.text
    assert gw.sent[0]["ID"] == payload["ID"]


def test_schema_mode_all_schemas_must_pass(api_client):
    """Add a second stricter schema that fails — request must be rejected."""
    client, gw, payloads_root = api_client
    strict = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"TestID": {"pattern": "^NEVER_MATCHES$"}},
        "required": ["TestID"],
    }
    _write_json(payloads_root / "AAA" / "Schemas" / "strict.json", strict)

    r = client.post(
        "/api/v1/messages/schema/AAA", json=_valid_template_payload("AAA"), headers=_as_alice()
    )
    assert r.status_code == 400
    assert gw.sent == []  # nothing forwarded


def test_schema_mode_no_schemas_rejects(tmp_path):
    """A project dir without any Schemas/*.json must be refused."""
    from app import api, main
    from app.payload_store import PayloadStore

    payloads_root = tmp_path / "payloads"
    (payloads_root / "AAA").mkdir(parents=True)  # no Schemas/

    stub_gateway = _StubGateway()
    api.set_gateway_client(stub_gateway)
    api.set_key_manager(_StubKeyManager({"alice": "AAA"}))
    api.set_payload_store(PayloadStore(payloads_dir=payloads_root))
    main.gateway_client = stub_gateway

    client = TestClient(main.app)
    r = client.post(
        "/api/v1/messages/schema/AAA", json=_valid_template_payload("AAA"), headers=_as_alice()
    )
    assert r.status_code == 400
    assert stub_gateway.sent == []


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------


def test_list_templates(api_client):
    client, _, payloads_root = api_client
    _write_json(payloads_root / "AAA" / "regression.json", _valid_template_payload("AAA"))

    r = client.get("/api/v1/payloads/AAA", headers=_as_alice())
    assert r.status_code == 200
    data = r.json()
    assert data["project"] == "AAA"
    assert set(data["templates"]) == {"smoke", "regression"}


def test_list_schemas(api_client):
    client, _, payloads_root = api_client
    _write_json(payloads_root / "AAA" / "Schemas" / "second.json", _PERMISSIVE_SCHEMA)

    r = client.get("/api/v1/payloads/AAA/schemas", headers=_as_alice())
    assert r.status_code == 200
    assert set(r.json()["schemas"]) == {"base", "second"}
