"""
Mock Certificate Gateway for testing.

Simulates the external certificate gateway that wraps messages with JWT tokens.
In production, this service is managed by another team.

Endpoints:
  POST /wrap  - Accepts a message JSON, returns it wrapped in a JWT envelope
  GET /health - Health check
"""

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Certificate Gateway", version="1.0.0")

# Default cert validity period (seconds)
CERT_VALIDITY_SECONDS = 300  # 5 minutes


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/wrap")
async def wrap_message(request: Request):
    """
    Wrap a message payload with a mock JWT token.

    Accepts any JSON body and returns:
    {
        "token": "<mock JWT string>",
        "expires_at": "<ISO 8601 UTC timestamp>",
        "message": {<original message>}
    }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid JSON body"},
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=CERT_VALIDITY_SECONDS)

    # Build a mock JWT token (header.payload.signature format, base64-like)
    message_id = body.get("ID", str(uuid.uuid4()))
    mock_token = (
        f"eyJhbGciOiJSUzI1NiJ9.{message_id}.mock_signature_{uuid.uuid4().hex[:16]}"
    )

    wrapped = {
        "token": mock_token,
        "expires_at": expires_at.isoformat(),
        "message": body,
    }

    print(f"[CERT] Wrapped message {message_id}, expires {expires_at.isoformat()}")

    return JSONResponse(status_code=status.HTTP_200_OK, content=wrapped)
