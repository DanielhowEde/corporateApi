"""
Mock DMZ Gateway for testing.

This simulates the gateway that sits between corporate and low-side networks.
Run on port 8000 (the default GATEWAY_URL).

Message routing:
  Messages received are forwarded to BOTH sides so each side can store them.
  In production the gateway would route based on certificates/headers.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock DMZ Gateway", version="1.0.0")

# Store received messages for inspection
RECEIVED_DIR = Path("./received")
RECEIVED_DIR.mkdir(exist_ok=True)

# Downstream service URLs (configurable via environment variables)
LOW_SIDE_URL = os.environ.get("LOW_SIDE_URL", "http://localhost:8002")
CORPORATE_URL = os.environ.get("CORPORATE_URL", "http://localhost:8001")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mock-gateway"}


async def _forward_message(url: str, body: dict, label: str) -> None:
    """Forward a message to a downstream service (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=body)
        if response.status_code < 300:
            print(f"[GATEWAY] Forwarded to {label}: {response.status_code}")
        else:
            print(
                f"[GATEWAY] {label} rejected message: {response.status_code} — {response.text[:120]}"
            )
    except Exception as e:
        print(f"[GATEWAY] Could not forward to {label}: {e}")


async def _handle_message(request: Request):
    """Common handler for /message and /messages endpoints."""
    try:
        body = await request.json()

        # If the payload is a JWT-wrapped envelope from the cert gateway
        # ({token, expires_at, message}), unwrap it so downstream receivers
        # see the raw Message schema. In production the gateway would also
        # verify the JWT signature and expiry here.
        if isinstance(body, dict) and "token" in body and "message" in body:
            print(f"[GATEWAY] Unwrapping JWT envelope (expires_at={body.get('expires_at')})")
            inner = body["message"]
        else:
            inner = body

        message_id = inner.get("ID", "unknown")
        project = inner.get("Project", "UNK")

        print(f"[GATEWAY] Received message: ID={message_id}, Project={project}")
        print(f"[GATEWAY] Payload: {json.dumps(inner, indent=2)}")

        # Save locally for inspection (save the unwrapped form)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = RECEIVED_DIR / f"{timestamp}_{project}_{message_id[:8]}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(inner, f, indent=2)
        print(f"[GATEWAY] Saved to: {filename}")

        # Forward the unwrapped message to both sides
        await _forward_message(f"{LOW_SIDE_URL}/dmz/messages", inner, "low-side")
        await _forward_message(f"{CORPORATE_URL}/dmz/messages", inner, "corporate")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "message_id": message_id, "forwarded": True},
        )

    except Exception as e:
        print(f"[GATEWAY] Error: {e}")
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)})


@app.post("/message")
async def receive_message_singular(request: Request):
    """Receive a message (legacy singular path)."""
    return await _handle_message(request)


@app.post("/messages")
async def receive_message_plural(request: Request):
    """Receive a message from corporate or low-side."""
    return await _handle_message(request)


@app.get("/messages")
async def list_messages():
    """List all received messages (for debugging)."""
    files = sorted(RECEIVED_DIR.glob("*.json"), reverse=True)
    messages = []
    for f in files[:20]:  # Last 20
        with open(f, encoding="utf-8") as fp:
            messages.append({"file": f.name, "content": json.load(fp)})
    return {"count": len(files), "recent": messages}


@app.post("/users")
async def sync_user(request: Request):
    """
    Forward a user sync event from corporate to the low-side.

    Corporate admin calls this when a user is created, updated, enabled,
    disabled, or deleted. The gateway forwards to low-side /dmz/users.
    """
    try:
        body = await request.json()
        username = body.get("username", "unknown")
        action = body.get("action", "upsert")
        print(f"[GATEWAY] User sync: username={username}, action={action}")

        # Forward to low-side
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{LOW_SIDE_URL}/dmz/users", json=body)
            if response.status_code < 300:
                print(f"[GATEWAY] User sync forwarded to low-side: {username}")
            else:
                print(f"[GATEWAY] Low-side rejected user sync: {response.status_code}")
        except Exception as forward_err:
            print(f"[GATEWAY] Could not forward user sync to low-side: {forward_err}")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "username": username},
        )

    except Exception as e:
        print(f"[GATEWAY] User sync error: {e}")
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)})


@app.post("/ca")
async def sync_ca(request: Request):
    """Forward a CA certificate sync from corporate to low-side."""
    try:
        body = await request.json()
        print(f"[GATEWAY] CA sync: {len(body.get('ca_pem', ''))} bytes")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{LOW_SIDE_URL}/dmz/ca", json=body)
            if response.status_code < 300:
                print("[GATEWAY] CA cert forwarded to low-side")
            else:
                print(f"[GATEWAY] Low-side rejected CA: {response.status_code}")
        except Exception as forward_err:
            print(f"[GATEWAY] Could not forward CA to low-side: {forward_err}")

        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "accepted"})
    except Exception as e:
        print(f"[GATEWAY] CA sync error: {e}")
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)})


@app.post("/client-certs")
async def sync_client_cert(request: Request):
    """Forward a client cert sync from corporate to low-side."""
    try:
        body = await request.json()
        key_id = body.get("key_id", "unknown")
        action = body.get("action", "upsert")
        print(f"[GATEWAY] Client cert sync: key_id={key_id}, action={action}")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{LOW_SIDE_URL}/dmz/client-certs", json=body)
            if response.status_code < 300:
                print(f"[GATEWAY] Client cert forwarded to low-side: {key_id}")
            else:
                print(f"[GATEWAY] Low-side rejected client cert: {response.status_code}")
        except Exception as forward_err:
            print(f"[GATEWAY] Could not forward client cert to low-side: {forward_err}")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "key_id": key_id},
        )
    except Exception as e:
        print(f"[GATEWAY] Client cert sync error: {e}")
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)})


@app.post("/audit/failed-login")
async def forward_failed_login(request: Request):
    """Forward a failed-login audit event from low-side to corporate."""
    try:
        body = await request.json()
        username = body.get("username", "unknown")
        print(f"[GATEWAY] Audit event: failed login for {username}")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{CORPORATE_URL}/dmz/audit/failed-login", json=body)
            if response.status_code < 300:
                print(f"[GATEWAY] Audit event forwarded to corporate: {username}")
            else:
                print(f"[GATEWAY] Corporate rejected audit: {response.status_code}")
        except Exception as forward_err:
            print(f"[GATEWAY] Could not forward audit event: {forward_err}")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "username": username},
        )
    except Exception as e:
        print(f"[GATEWAY] Audit event error: {e}")
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": str(e)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
