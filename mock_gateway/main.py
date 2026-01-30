"""
Mock DMZ Gateway for testing.

This simulates the gateway that sits between corporate and low-side networks.
Run on port 8000 (the default GATEWAY_URL).
"""
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock DMZ Gateway", version="1.0.0")

# Store received messages for inspection
RECEIVED_DIR = Path("./received")
RECEIVED_DIR.mkdir(exist_ok=True)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mock-gateway"}


@app.post("/message")
async def receive_message(request: Request):
    """
    Receive a message from corporate side.

    In production, this would forward to the low-side.
    For testing, we just log it and return success.
    """
    try:
        body = await request.json()
        message_id = body.get("ID", "unknown")
        project = body.get("Project", "UNK")

        # Log the message
        print(f"[GATEWAY] Received message: ID={message_id}, Project={project}")
        print(f"[GATEWAY] Full payload: {json.dumps(body, indent=2)}")

        # Save to file for inspection
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = RECEIVED_DIR / f"{timestamp}_{project}_{message_id[:8]}.json"
        with open(filename, "w") as f:
            json.dump(body, f, indent=2)
        print(f"[GATEWAY] Saved to: {filename}")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "accepted",
                "message_id": message_id,
                "forwarded": True
            }
        )

    except Exception as e:
        print(f"[GATEWAY] Error: {e}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": str(e)}
        )


@app.get("/messages")
async def list_messages():
    """List all received messages (for debugging)."""
    files = sorted(RECEIVED_DIR.glob("*.json"), reverse=True)
    messages = []
    for f in files[:20]:  # Last 20
        with open(f) as fp:
            messages.append({
                "file": f.name,
                "content": json.load(fp)
            })
    return {"count": len(files), "recent": messages}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
