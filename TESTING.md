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
| `corporate/data/messages/ABC/` | Messages stored by Corporate API |
| `mock_gateway/received/` | Messages received by mock gateway |
| `corporate/data/whitelist.json` | Project whitelist |
| `corporate/data/users.json` | User accounts |

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
