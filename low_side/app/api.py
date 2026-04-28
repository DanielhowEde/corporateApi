"""
Programmatic API (/api/v1) for external callers on the low-side.

Mirror of the corporate /api/v1 surface. Authentication is mTLS — the
reverse proxy passes the client cert's CN via header, and we look the CN
up in the synced client-cert store to find the bound project.
"""

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .gateway_client import GatewayError, GatewayUnavailableError
from .models import ErrorResponse, SuccessResponse
from .payload_store import PayloadStore, PayloadStoreError
from .utils import setup_logging

logger = setup_logging("api")

router = APIRouter(prefix="/api/v1", tags=["Programmatic API"])

gateway_client = None
cert_store = None
payload_store: PayloadStore | None = None


def set_gateway_client(client) -> None:
    """Wire the gateway client used to forward outgoing messages."""
    global gateway_client
    gateway_client = client


def set_cert_store(store) -> None:
    """Wire the cert store used for CN -> project lookup."""
    global cert_store
    cert_store = store


def set_payload_store(store: PayloadStore) -> None:
    """Wire the payload store used to load templates and schemas."""
    global payload_store
    payload_store = store


_PROJECT_RE = re.compile(r"^[A-Z0-9]{3}$")


def _extract_cn(request: Request) -> str | None:
    """Read the authenticated client cert's CN from reverse-proxy headers."""
    cn = request.headers.get("x-client-cert-cn")
    if cn:
        return cn.strip()

    dn = request.headers.get("x-ssl-client-s-dn")
    if dn:
        match = re.search(r"CN=([^,/]+)", dn)
        if match:
            return match.group(1).strip()

    return None


def _require_api_project(request: Request, project: str) -> str:
    """Resolve and authorise the caller for the given project (see corporate.api)."""
    if not _PROJECT_RE.match(project or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid project")

    cn = _extract_cn(request)
    if not cn:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client cert required")

    if cert_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cert store not configured"
        )

    bound_project = cert_store.get_project_for_cn(cn)
    if not bound_project or bound_project != project.upper():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project not authorised")

    return cn


def _fresh_id_and_date(payload: dict[str, Any]) -> dict[str, Any]:
    """Regenerate ID (UUID4) + Date (now, ISO 8601) on a payload copy."""
    payload = dict(payload)
    payload["ID"] = str(uuid.uuid4())
    payload["Date"] = datetime.now().replace(microsecond=0).isoformat()
    return payload


def _error(request_id: str, http_status: int, reason: str) -> JSONResponse:
    logger.warning(f"API rejected: status={http_status}, reason={reason}")
    return JSONResponse(
        status_code=http_status,
        content=ErrorResponse(request_id=request_id, error="Invalid request").model_dump(),
    )


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


@router.get("/payloads/{project}", summary="List payload templates for a project")
async def list_templates(project: str, request: Request) -> dict[str, Any]:
    _require_api_project(request, project)
    return {"project": project.upper(), "templates": payload_store.list_payloads(project.upper())}


@router.get("/payloads/{project}/schemas", summary="List JSON Schemas for a project")
async def list_project_schemas(project: str, request: Request) -> dict[str, Any]:
    _require_api_project(request, project)
    return {"project": project.upper(), "schemas": payload_store.list_schemas(project.upper())}


# ----------------------------------------------------------------------
# Template mode
# ----------------------------------------------------------------------


@router.post(
    "/messages/template/{project}/{template_name}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Template not found"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
    summary="Send a message built from a server-side template",
)
async def send_template(
    project: str,
    template_name: str,
    request: Request,
    body: dict[str, Any] | None = None,
):
    request_id = request.state.request_id
    project = project.upper()
    _require_api_project(request, project)

    body = body or {}
    overrides = body.get("overrides") or {}
    verbatim = bool(body.get("verbatim"))
    if not isinstance(overrides, dict):
        return _error(request_id, status.HTTP_400_BAD_REQUEST, "overrides must be an object")

    try:
        template = payload_store.load_payload(project, template_name)
    except PayloadStoreError as exc:
        return _error(request_id, status.HTTP_404_NOT_FOUND, str(exc))

    payload = {**template, **overrides}
    if not verbatim:
        payload = _fresh_id_and_date(payload)
    payload["Project"] = project

    return await _validate_and_send(request_id, project, payload)


# ----------------------------------------------------------------------
# Schema mode
# ----------------------------------------------------------------------


@router.post(
    "/messages/schema/{project}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
    summary="Send a caller-supplied payload validated against project schemas",
)
async def send_schema(project: str, request: Request, payload: dict[str, Any]):
    request_id = request.state.request_id
    project = project.upper()
    _require_api_project(request, project)

    if not isinstance(payload, dict):
        return _error(request_id, status.HTTP_400_BAD_REQUEST, "payload must be an object")

    payload = {**payload, "Project": project}

    return await _validate_and_send(request_id, project, payload)


# ----------------------------------------------------------------------
# Shared validate + send
# ----------------------------------------------------------------------


async def _validate_and_send(
    request_id: str, project: str, payload: dict[str, Any]
) -> JSONResponse:
    """
    Validate against the project's JSON Schemas and forward via gateway.

    Project schemas are the sole authority on payload shape. ID is
    auto-synthesised when absent so the response always carries one.
    """
    ok, errs = payload_store.validate_payload(project, payload)
    if not ok:
        logger.warning(f"Project schema validation failed: project={project}, errors={errs}")
        return _error(request_id, status.HTTP_400_BAD_REQUEST, "; ".join(errs))

    if gateway_client is None:
        return _error(request_id, status.HTTP_503_SERVICE_UNAVAILABLE, "Gateway not configured")

    message_id = str(payload.get("ID") or uuid.uuid4())
    payload.setdefault("ID", message_id)

    try:
        await gateway_client.send_message(payload)
    except GatewayUnavailableError as exc:
        logger.error(f"Gateway unavailable: {exc}")
        return _error(request_id, status.HTTP_503_SERVICE_UNAVAILABLE, "Gateway unavailable")
    except GatewayError as exc:
        logger.error(f"Gateway error: {exc}")
        return _error(request_id, status.HTTP_400_BAD_REQUEST, "Gateway rejected message")

    logger.info(f"API sent message: project={project}, id={message_id}")
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=SuccessResponse(request_id=request_id, message_id=message_id).model_dump(),
    )
