# Testing Guide

## Prerequisites

```bash
pip install fastapi uvicorn jinja2 httpx pydantic python-multipart
```

## Running the Services

Open three terminals and run each service:

### Terminal 1: Mock Gateway (port 8000)
```bash
cd mock_gateway
uvicorn main:app --reload --port 8000
```

### Terminal 2: Corporate API (port 8001)
```bash
cd corporate
uvicorn app.main:app --reload --port 8001
```

### Terminal 3: Low-Side API (port 8002)
```bash
cd low_side
uvicorn app.main:app --reload --port 8002
```

## URLs

| Service | URL |
|---------|-----|
| Mock Gateway | http://localhost:8000 |
| Corporate Admin | http://localhost:8001/admin/ |
| Corporate User Portal | http://localhost:8001/user/ |
| Corporate API Docs | http://localhost:8001/docs |
| Low-Side User Portal | http://localhost:8002/user/ |
| Low-Side API Docs | http://localhost:8002/docs |

## Test Workflow

### 1. Setup (Admin)

1. Go to http://localhost:8001/admin/
2. Login with password: `admin123`
3. **Add a project:**
   - Navigate to Projects
   - Add project code `ABC` (enabled)
4. **Create a user:**
   - Navigate to Users
   - Create user `testuser` with password `test123`

### 2. User Login & Password Change

1. Go to http://localhost:8001/user/
2. Login with `testuser` / `test123`
3. You'll be redirected to change password (required on first login)
4. Set a new password

### 3. Send a Message

1. Go to Send Message
2. Fill in the form:
   - **ID**: Auto-generated UUID
   - **Project**: Select `ABC`
   - **TestID**: Any value (e.g., `TEST001`)
   - **Area**: Any value (e.g., `QA`)
   - **Status**: Any value (e.g., `PASS`)
   - **Date**: Auto-generated
   - **Data**: `{"result": "success", "score": 100}`
3. Click Send

### 4. Verify

Check the mock gateway terminal - you should see:
```
[GATEWAY] Received message: ID=..., Project=ABC
[GATEWAY] Saved to: ./received/...json
```

Or visit http://localhost:8000/messages to see received messages.

## Low-Side User Portal

Users on the low-side can also send messages. Their accounts are created by the corporate
admin and synced automatically through the gateway.

### 5. Low-Side User Login

1. Go to http://localhost:8002/user/
2. Login with the same `testuser` credentials created in step 1
   - On first login you will be asked to change your password
3. From the home page, click **Send Message**

### 6. Send a Message from Low-Side

1. Fill in the form (same schema as corporate):
   - **ID**: Auto-generated UUID
   - **Project**: Type `ABC` (must match a project authorized on corporate)
   - **Test ID**: e.g., `LST001`
   - **Test Status**: e.g., `Pass`
   - **Timestamp**: Auto-filled
   - **Data**: `{"source": "low-side", "result": "pass"}`
2. Click Send — the message goes to the mock gateway

Check the mock gateway terminal:
```
[GATEWAY] Received message: ID=..., Project=ABC
[GATEWAY] Saved to: ./received/...json
```

### How User Sync Works

When the corporate admin creates, enables, disables, or resets a user password:
1. Corporate calls the gateway `POST /users`
2. The mock gateway forwards to low-side `POST /dmz/users`
3. Low-side stores the user in `low_side/data/users.json`

The user can then log in at `http://localhost:8002/user/` with their corporate credentials.

## API Testing with curl

### Health Check
```bash
curl http://localhost:8001/health
```

### Send Message via API
```bash
curl -X POST http://localhost:8001/message \
  -H "Content-Type: application/json" \
  -d '{
    "ID": "550e8400-e29b-41d4-a716-446655440000",
    "Project": "ABC",
    "TestID": "TEST001",
    "Area": "QA",
    "Status": "PASS",
    "Date": "30012026T10:00:00",
    "Data": {"result": "success"}
  }'
```

### List Gateway Messages
```bash
curl http://localhost:8000/messages
```

## File Locations

After sending messages:

| Location | Contents |
|----------|----------|
| `mock_gateway/received/` | All messages received by gateway (both directions) |
| `corporate/data/messages/ABC/` | Messages forwarded to corporate by gateway |
| `low_side/data/messages/ABC/` | Messages forwarded to low-side by gateway |
| `corporate/data/whitelist.json` | Project whitelist |
| `corporate/data/users.json` | User accounts (corporate) |
| `low_side/data/users.json` | User accounts synced from corporate via gateway |

**Note:** The mock gateway forwards every received message to both corporate `/dmz/messages`
and low-side `/dmz/messages`. Corporate requires the project to be whitelisted to store it.
Low-side stores all valid messages it receives.

## Configuration

Create `corporate/config.json` to customize:

```json
{
  "COMPANY_NAME": "Acme Corp",
  "SERVICE_NAME": "Secure Gateway",
  "NETWORK_LABEL": "ACME INTERNAL",
  "ADMIN_PASSWORD": "your_secure_password",
  "GATEWAY_URL": "http://localhost:8000",
  "MASTER_DIR": "./data/messages"
}
```

Or use environment variables:
```bash
export COMPANY_NAME="Acme Corp"
export ADMIN_PASSWORD="secure123"
```

## Troubleshooting

### "Project not authorized"
- Add the project code in Admin → Projects

### "Gateway unavailable"
- Make sure mock gateway is running on port 8000

### "Invalid username or password"
- Check user exists in Admin → Users
- User might be disabled

### Password change loop
- User has `must_change_password` flag set
- Complete the password change form
