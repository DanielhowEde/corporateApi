"""
Tests for the programmatic API (/api/v1) on the low-side service.

Auth is mTLS — reverse proxy sets the client cert CN in a header, and we
resolve CN -> project via the synced cert_store.
"""

import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient


def _valid_template_payload(project: str = "AAA") -> dict:
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
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, message_data):
        self.sent.append(dict(message_data))
        return {"success": True, "message_id": message_data.get("ID")}


class _StubCertStore:
    def __init__(self, bindings: dict[str, str]):
        self.bindings = bindings

    def get_project_for_cn(self, cn: str):
        return self.bindings.get(cn)


@pytest.fixture
def api_client(tmp_path):
    from app import api, main
    from app.payload_store import PayloadStore

    payloads_root = tmp_path / "payloads"
    _write_json(payloads_root / "AAA" / "smoke.json", _valid_template_payload("AAA"))
    _write_json(payloads_root / "AAA" / "Schemas" / "base.json", _PERMISSIVE_SCHEMA)

    stub_gateway = _StubGateway()
    stub_cs = _StubCertStore({"alice": "AAA"})

    main.gateway_client = stub_gateway
    api.set_gateway_client(stub_gateway)
    api.set_cert_store(stub_cs)
    api.set_payload_store(PayloadStore(payloads_dir=payloads_root))

    client = TestClient(main.app)
    return client, stub_gateway, payloads_root


def _as_alice(headers: dict | None = None) -> dict:
    return {"X-Client-Cert-CN": "alice", **(headers or {})}


# ---------------------------------------------------------------------


def test_request_without_cn_header_is_forbidden(api_client):
    client, _, _ = api_client
    r = client.post("/api/v1/messages/template/AAA/smoke", json={})
    assert r.status_code == 403


def test_cn_unbound_to_project_is_forbidden(api_client):
    client, _, payloads_root = api_client
    _write_json(payloads_root / "BBB" / "smoke.json", _valid_template_payload("BBB"))
    _write_json(payloads_root / "BBB" / "Schemas" / "base.json", _PERMISSIVE_SCHEMA)

    r = client.post("/api/v1/messages/template/BBB/smoke", json={}, headers=_as_alice())
    assert r.status_code == 403


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
    uuid.UUID(sent["ID"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", sent["Date"])


def test_template_verbatim_preserves_id_and_date(api_client):
    client, gw, payloads_root = api_client
    template = _valid_template_payload("AAA")
    fixed = str(uuid.uuid4())
    template["ID"] = fixed
    template["Date"] = "2024-06-01T12:00:00"
    _write_json(payloads_root / "AAA" / "smoke.json", template)

    r = client.post(
        "/api/v1/messages/template/AAA/smoke",
        json={"verbatim": True},
        headers=_as_alice(),
    )
    assert r.status_code == 200, r.text
    assert gw.sent[0]["ID"] == fixed
    assert gw.sent[0]["Date"] == "2024-06-01T12:00:00"


def test_schema_mode_sends_valid_payload(api_client):
    client, gw, _ = api_client
    payload = _valid_template_payload("AAA")

    r = client.post("/api/v1/messages/schema/AAA", json=payload, headers=_as_alice())
    assert r.status_code == 200, r.text
    assert gw.sent[0]["ID"] == payload["ID"]


def test_schema_mode_all_schemas_must_pass(api_client):
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
    assert gw.sent == []


def test_list_templates(api_client):
    client, _, payloads_root = api_client
    _write_json(payloads_root / "AAA" / "regression.json", _valid_template_payload("AAA"))

    r = client.get("/api/v1/payloads/AAA", headers=_as_alice())
    assert r.status_code == 200
    assert set(r.json()["templates"]) == {"smoke", "regression"}


def test_list_schemas(api_client):
    client, _, payloads_root = api_client
    _write_json(payloads_root / "AAA" / "Schemas" / "second.json", _PERMISSIVE_SCHEMA)

    r = client.get("/api/v1/payloads/AAA/schemas", headers=_as_alice())
    assert r.status_code == 200
    assert set(r.json()["schemas"]) == {"base", "second"}
