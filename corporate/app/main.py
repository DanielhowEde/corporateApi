"""
CORPORATE DMZ API Service.

This service handles message exchange with the DMZ Gateway for the corporate network.

Security Assumptions:
- mTLS is enforced by the reverse proxy (Nginx/Envoy) in front of this service
- All incoming requests have been authenticated via client certificates
- The /dmz/messages endpoint should only accept requests from the Gateway
  (enforced by proxy configuration; placeholder middleware included for future cert checks)

Corporate-Specific Features:
- Project whitelist enforcement via SQLite database
- Projects must be present and enabled to send/receive messages
"""
import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .file_store import FileStore, FileStoreError
from .gateway_client import GatewayClient, GatewayError, GatewayUnavailableError
from .models import ErrorResponse, HealthResponse, Message, SuccessResponse
from .utils import generate_request_id, set_request_id, setup_logging
from .whitelist import ProjectWhitelist

# Configure logging
logger = setup_logging("corporate_api")

# Configuration from environment
DATA_DIR = os.environ.get("DATA_DIR", "./data")

# Global instances (initialized in lifespan)
file_store: FileStore
gateway_client: GatewayClient
whitelist: ProjectWhitelist


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    global file_store, gateway_client, whitelist

    # Startup
    logger.info("Starting CORPORATE DMZ API")
    file_store = FileStore(data_dir=DATA_DIR)
    gateway_client = GatewayClient()
    whitelist = ProjectWhitelist()
    logger.info(f"File store initialized at: {DATA_DIR}")
    logger.info(f"Gateway URL: {gateway_client.base_url}")
    logger.info(f"Whitelist DB: {whitelist.db_path}")

    yield

    # Shutdown
    logger.info("Shutting down CORPORATE DMZ API")
    await gateway_client.close()
    whitelist.close()


app = FastAPI(
    title="CORPORATE DMZ API",
    description="Corporate-side API for secure message exchange via DMZ Gateway",
    version="1.0.0",
    lifespan=lifespan
)


# =============================================================================
# Middleware
# =============================================================================

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Add request ID to all requests for tracking."""
    request_id = generate_request_id()
    set_request_id(request_id)
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


async def verify_gateway_origin(request: Request) -> bool:
    """
    Placeholder middleware hook for verifying requests originate from the Gateway.

    In production, this would check:
    - Client certificate fingerprint from headers (set by reverse proxy)
    - Specific header values indicating Gateway origin

    Currently, this is enforced by the reverse proxy configuration.

    TODO: Implement certificate fingerprint validation when header format is defined.

    Args:
        request: The incoming request

    Returns:
        True if the request is from the Gateway, False otherwise
    """
    # Placeholder: In production, check headers set by reverse proxy
    # Example headers that might be set by proxy:
    # - X-Client-Cert-Fingerprint
    # - X-Client-Cert-DN
    # - X-Gateway-Authenticated

    # For now, assume proxy has done the validation
    return True


def check_project_whitelist(project_code: str) -> bool:
    """
    Check if a project is allowed by the whitelist.

    Args:
        project_code: The 3-character project code

    Returns:
        True if the project is whitelisted and enabled
    """
    return whitelist.is_project_allowed(project_code)


# =============================================================================
# Error Handlers
# =============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with generic error response."""
    request_id = getattr(request.state, "request_id", "-")
    logger.error(f"HTTP exception: status={exc.status_code}, detail={exc.detail}")

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            request_id=request_id,
            error="Invalid request"
        ).model_dump()
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with generic error response."""
    request_id = getattr(request.state, "request_id", "-")
    logger.exception(f"Unexpected exception: {exc}")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            request_id=request_id,
            error="Invalid request"
        ).model_dump()
    )


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns:
        HealthResponse with status "ok"
    """
    return HealthResponse(status="ok")


@app.post(
    "/messages",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        503: {"model": ErrorResponse, "description": "Service unavailable"}
    },
    tags=["Messages"]
)
async def send_message(request: Request, message: Dict[str, Any]) -> SuccessResponse:
    """
    Send a message to the DMZ Gateway (outbound).

    This endpoint validates the message schema, checks the project whitelist,
    and forwards the message to the Gateway.

    **Security:**
    - Requires mTLS authentication (enforced by reverse proxy)
    - Project must be whitelisted and enabled

    Args:
        request: The HTTP request
        message: The message to send (validated against Message schema)

    Returns:
        SuccessResponse with request_id and message_id on success

    Raises:
        400: Invalid message schema or project not whitelisted
        503: Gateway unavailable
    """
    request_id = request.state.request_id

    # Validate message schema
    try:
        validated_message = Message(**message)
    except ValidationError as e:
        logger.warning(f"Schema validation failed: errors={e.errors()}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    message_id = validated_message.ID
    project_code = validated_message.Project

    # Check project whitelist
    if not check_project_whitelist(project_code):
        logger.warning(
            f"Project not whitelisted: message_id={message_id}, project={project_code}"
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    logger.info(f"Sending message to gateway: message_id={message_id}")

    # Forward to gateway
    try:
        await gateway_client.send_message(validated_message.model_dump())
        logger.info(f"Message sent successfully: message_id={message_id}")

        return SuccessResponse(
            request_id=request_id,
            message_id=message_id
        )

    except GatewayUnavailableError as e:
        logger.error(f"Gateway unavailable: message_id={message_id}, error={e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    except GatewayError as e:
        logger.error(f"Gateway error: message_id={message_id}, error={e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )


@app.post(
    "/dmz/messages",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    },
    tags=["DMZ"]
)
async def receive_message(request: Request, message: Dict[str, Any]) -> SuccessResponse:
    """
    Receive a message from the DMZ Gateway (inbound).

    This endpoint validates the message schema, checks the project whitelist,
    and writes the message to disk atomically.

    **Security:**
    - Requires mTLS authentication (enforced by reverse proxy)
    - Only accepts requests from Gateway certificates (enforced by proxy)
    - Project must be whitelisted and enabled

    Args:
        request: The HTTP request
        message: The message received from the Gateway

    Returns:
        SuccessResponse with request_id and message_id on success

    Raises:
        400: Invalid message schema or project not whitelisted
        500: Disk write failure
    """
    request_id = request.state.request_id

    # Verify request is from Gateway (placeholder - enforced by proxy)
    if not await verify_gateway_origin(request):
        logger.warning("Request rejected: not from Gateway origin")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    # Validate message schema
    try:
        validated_message = Message(**message)
    except ValidationError as e:
        logger.warning(f"Schema validation failed: errors={e.errors()}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    message_id = validated_message.ID
    project_code = validated_message.Project

    # Check project whitelist
    if not check_project_whitelist(project_code):
        logger.warning(
            f"Project not whitelisted: message_id={message_id}, project={project_code}"
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(request_id=request_id).model_dump()
        )

    logger.info(f"Receiving message from gateway: message_id={message_id}")

    # Write to disk atomically
    try:
        file_path = file_store.write_message(validated_message.model_dump())
        logger.info(f"Message written to disk: message_id={message_id}, path={file_path}")

        return SuccessResponse(
            request_id=request_id,
            message_id=message_id
        )

    except FileStoreError as e:
        logger.error(f"Failed to write message: message_id={message_id}, error={e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(request_id=request_id).model_dump()
        )
