# DMZ Message Exchange API

Secure message exchange system consisting of two FastAPI services: **LOW-SIDE API** and **CORPORATE API**.

## Architecture Overview

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  LOW-SIDE   │◄────►│   GATEWAY   │◄────►│  CORPORATE  │
│    API      │ mTLS │   (DMZ)     │ mTLS │    API      │
└─────────────┘      └─────────────┘      └─────────────┘
      │                                          │
      ▼                                          ▼
   ./data/                                    ./data/
   incoming/                                  incoming/
```

Both services can send messages to and receive messages from the DMZ Gateway.

## Security

- **mTLS Required**: All communication requires mutual TLS authentication, enforced by a reverse proxy (Nginx/Envoy)
- **Generic Error Messages**: All error responses are generic to prevent information leakage
- **Request Tracking**: Every request gets a unique `request_id` for audit logging
- **Gateway Origin Verification**: The `/dmz/messages` endpoint only accepts requests from Gateway certificates

## Project Structure

```
repo/
├── low_side/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── models.py         # Pydantic models & validation
│   │   ├── file_store.py     # Atomic file writing
│   │   ├── gateway_client.py # HTTP client for Gateway
│   │   └── utils.py          # Utilities & logging
│   ├── tests/
│   └── requirements.txt
├── corporate/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── models.py         # Pydantic models & validation
│   │   ├── file_store.py     # Atomic file writing + error storage
│   │   ├── gateway_client.py # HTTP client for Gateway (with cert wrapping)
│   │   ├── cert_client.py    # HTTP client for Certificate Gateway (JWT)
│   │   ├── whitelist.py      # Project whitelist (JSON file)
│   │   ├── admin.py          # Admin web interface routes
│   │   ├── user.py           # User web interface routes
│   │   ├── templates/        # HTML templates for web UIs
│   │   └── utils.py          # Utilities & logging
│   ├── scripts/
│   │   └── whitelist_admin.py # CLI tool for whitelist management
│   ├── tests/
│   └── requirements.txt
├── api-contracts/
│   ├── low-side-api.yaml     # OpenAPI spec for LOW-SIDE
│   └── corporate-api.yaml    # OpenAPI spec for CORPORATE
├── cleanup.sh                # Removes JSON files older than 1 day
└── README.md
```

## Message Schema

Both services use the same message schema:

```json
{
  "ID": "550e8400-e29b-41d4-a716-446655440000",
  "Project": "AAA",
  "TestID": "AAA-1112",
  "Area": "Area Name",
  "Date": "2026-01-30T11:22:33",
  "Status": "Inprogress",
  "Data": {
    "result": "pass",
    "note": "all checks passed"
  }
}
```

### Validation Rules

| Field | Rule |
|-------|------|
| ID | Valid UUID |
| Project | Exactly 3 uppercase alphanumeric characters (`^[A-Z0-9]{3}$`) |
| TestID | 3-10 characters |
| Area | 3-64 characters |
| Date | ISO 8601 datetime (e.g. `2026-01-30T11:22:33`) |
| Status | Free text |
| Data | Object with string values only; max 20 entries; values 1-128 chars; allowed chars: `a-z A-Z 0-9 space ;` |
| Top-level | No extra fields allowed (strict schema) |

## API Endpoints

### Both Services

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/messages` | Send message to Gateway (outbound) |
| POST | `/dmz/messages` | Receive message from Gateway (inbound) |

### Responses

**Success (200)**:
```json
{
  "success": true,
  "request_id": "uuid",
  "message_id": "uuid"
}
```

**Error (400/500/503)**:
```json
{
  "success": false,
  "request_id": "uuid",
  "error": "Invalid request"
}
```

## Installation & Running

### LOW-SIDE API

```bash
cd low_side

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Run the service
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run with reload for development
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### CORPORATE API

```bash
cd corporate

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Initialize whitelist with some projects
python scripts/whitelist_admin.py add AAA
python scripts/whitelist_admin.py add BBB
python scripts/whitelist_admin.py list

# Run the service
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Run with reload for development
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## Configuration

There are three ways to set addresses and paths, in priority order (highest wins):

1. **Environment variables** (recommended for deployment)
2. **`config.json` file** in the service directory (recommended for persistent config)
3. **Defaults** baked into `config.py` (development only)

### Environment Variables

| Variable | Used by | Default | Description |
|----------|---------|---------|-------------|
| `GATEWAY_URL` | corporate, low-side | `http://localhost:8000` | DMZ Gateway base URL |
| `CERT_GATEWAY_URL` | corporate only | `https://cert-gateway.example.com` | Certificate gateway for JWT wrapping |
| `MASTER_DIR` | corporate, low-side | `./data/messages` | Directory for stored messages |
| `TMP_DIR` | corporate, low-side | `./data/tmp` | Temp directory for atomic writes |
| `ERROR_DIR` | corporate, low-side | `./data/errors` | Directory for error records |
| `KEYS_DIR` | corporate only | `./data/keys` | Directory for generated key pairs |
| `PENDING_DIR` | corporate only | `./data/pending` | Directory for pending messages (auto-send off) |
| `WHITELIST_FILE_PATH` | corporate only | `./data/whitelist.json` | Project whitelist JSON file |
| `USERS_FILE_PATH` | corporate, low-side | `./data/users.json` | User accounts JSON file |
| `ADMIN_PASSWORD` | corporate only | `admin123` | Initial admin password |
| `LOW_SIDE_URL` | mock gateway | `http://localhost:8002` | Where mock gateway forwards to low-side |
| `CORPORATE_URL` | mock gateway | `http://localhost:8001` | Where mock gateway forwards to corporate |

### Setting Environment Variables

**Windows (cmd):**
```cmd
set GATEWAY_URL=https://gateway.prod.example.com
set CERT_GATEWAY_URL=https://cert-gateway.prod.example.com:8443
python -m uvicorn app.main:app --port 8001
```

**Windows (PowerShell):**
```powershell
$env:GATEWAY_URL="https://gateway.prod.example.com"
$env:CERT_GATEWAY_URL="https://cert-gateway.prod.example.com:8443"
python -m uvicorn app.main:app --port 8001
```

**Linux/Mac:**
```bash
export GATEWAY_URL=https://gateway.prod.example.com
export CERT_GATEWAY_URL=https://cert-gateway.prod.example.com:8443
python -m uvicorn app.main:app --port 8001
```

### Using `config.json`

Create `corporate/config.json`:
```json
{
  "GATEWAY_URL": "https://gateway.prod.example.com",
  "CERT_GATEWAY_URL": "https://cert-gateway.prod.example.com:8443",
  "MASTER_DIR": "/var/data/corporate/messages",
  "ERROR_DIR": "/var/data/corporate/errors"
}
```

Create `low_side/config.json`:
```json
{
  "GATEWAY_URL": "https://gateway.prod.example.com",
  "MASTER_DIR": "/var/data/lowside/messages"
}
```

### Updating Defaults (development only)

- Corporate cert + gateway: `corporate/app/config.py` (`DEFAULTS` dict)
- Low-side gateway: `low_side/app/config.py` (`DEFAULTS` dict)

### Listener Address / Port

The service's own bind address is set on the `uvicorn` command line, **not** in config:

```bash
# Listen on all interfaces, port 8001
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# Listen on a specific IP
python -m uvicorn app.main:app --host 10.0.1.25 --port 8001

# Loopback only (default)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### Verifying the Config Took Effect

After starting a service, check the startup logs — the resolved URLs are printed:

```
INFO - Message store: /var/data/corporate/messages
INFO - Gateway URL: https://dmz-gateway.internal.yourorg.com
INFO - Whitelist file: /var/data/corporate/whitelist.json
```

### Restarting After Changes

Most config changes require a **restart** of the service. The one exception is the **project whitelist** (`whitelist.json`) which auto-reloads when the file's mtime changes — no restart needed for whitelist edits.

### Message Flow with Cert Gateway

When the corporate service sends a message:

```
1. User submits message → corporate /user/send
2. Corporate POSTs to {CERT_GATEWAY_URL}/wrap
3. Cert gateway returns { token, expires_at, message }
4. If auto-send: corporate POSTs wrapped payload to {GATEWAY_URL}/messages
   If auto-send off: wrapped payload saved to {PENDING_DIR} for manual release
```

## File Storage

Messages received via `/dmz/messages` are stored atomically:

```
{DATA_DIR}/incoming/YYYY/MM/DD/{message_id}.json
```

Example: A message with Date `30012026T11:22:33` and ID `550e8400-...` is stored at:
```
./data/incoming/2026/01/30/550e8400-e29b-41d4-a716-446655440000.json
```

### Atomic Write Process

1. Write to `{DATA_DIR}/tmp/{id}.json.tmp`
2. `fsync` to ensure data is on disk
3. Rename to final path (atomic on POSIX)

## Whitelist Management (Corporate Only)

The corporate API enforces a project whitelist using a JSON file.

### File Format

The whitelist is stored in `./data/whitelist.json` (configurable via `WHITELIST_FILE_PATH`):

```json
{
  "projects": {
    "AAA": {"enabled": true},
    "BBB": {"enabled": false},
    "CCC": {"enabled": true}
  }
}
```

You can edit this file directly with any text editor. Changes are detected automatically.

### CLI Tool

```bash
cd corporate

# Add a project (enabled by default)
python scripts/whitelist_admin.py add AAA

# Add a project but keep it disabled
python scripts/whitelist_admin.py add BBB --disabled

# Enable a project
python scripts/whitelist_admin.py enable BBB

# Disable a project
python scripts/whitelist_admin.py disable AAA

# Remove a project
python scripts/whitelist_admin.py remove BBB

# List all projects
python scripts/whitelist_admin.py list

# Check if a project is allowed
python scripts/whitelist_admin.py check AAA
```

### Runtime Updates

The whitelist can be updated at runtime without restarting the service:
- Edit the JSON file directly, or
- Use the CLI tool, or
- Use the Admin web interface

Changes are detected automatically via file modification time and take effect immediately.

## Admin Web Interface (Corporate Only)

The corporate API includes a web-based admin interface for managing projects and viewing certificate status.

### Accessing the Admin Interface

Navigate to `http://localhost:8001/admin/` (or your configured host/port).

### Admin Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/admin/` | Overview and quick links |
| Projects | `/admin/projects` | Add, enable, disable, remove projects |
| Certificates | `/admin/certs` | View certificate status and renewal info |
| Users | `/admin/users` | User management (v2 placeholder) |

### Features

- **Project Management**: Add new projects, enable/disable existing ones, remove projects
- **Certificate Visibility**: View active certificates and their expiration dates
- **No restart required**: Changes take effect immediately

### Security Note

The admin interface should be protected by mTLS like all other endpoints. Configure your reverse proxy to restrict `/admin/*` access to authorized admin certificates only.

Example Nginx configuration:
```nginx
location /admin/ {
    if ($ssl_client_s_dn !~ "CN=admin.corporate.example.com") {
        return 403;
    }
    proxy_pass http://127.0.0.1:8001;
}
```

## User Web Interface (Corporate Only)

The corporate API includes a user-facing web interface for sending messages manually.

### Accessing the User Interface

Navigate to `http://localhost:8001/user/` (or your configured host/port).

### User Pages

| Page | URL | Description |
|------|-----|-------------|
| Home | `/user/` | Welcome page and quick links |
| Send Message | `/user/send` | Compose and send messages to low-side |
| History | `/user/history` | View sent messages (coming soon) |

### Send Message Features

- **Project Selection**: Dropdown shows only authorized (whitelisted) projects
- **Auto-generated IDs**: UUID and current timestamp auto-populated
- **Schema Validation**: Form validates against message schema before sending
- **Custom Data**: JSON editor for arbitrary data payload

### Security Note

The user interface should be protected by mTLS. Configure your reverse proxy to restrict `/user/*` access to authorized user certificates.

## Testing

### LOW-SIDE Tests

```bash
cd low_side
pip install -r requirements.txt
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=app --cov-report=term-missing
```

### CORPORATE Tests

```bash
cd corporate
pip install -r requirements.txt
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=app --cov-report=term-missing
```

## Gateway Integration

The Gateway API is built by another team. These services communicate with it via:

- **Outbound**: `POST {GATEWAY_URL}/messages`
- **Inbound**: The Gateway calls our `/dmz/messages` endpoint

### Retry Logic

The gateway client implements automatic retries:
- 2 retries on timeout or 5xx errors
- Exponential backoff (0.5s, 1s)
- No retry on 4xx errors

## Logging

All requests are logged with:
- `request_id`: Unique identifier for tracking
- `message_id`: Message UUID (when available)
- Outcome (success/failure reason)

**Important**: Detailed error reasons are logged server-side only. Client responses remain generic.

Example log format:
```
2026-01-30 11:22:33 - corporate_api - INFO - [request_id=abc-123] - Message sent successfully: message_id=550e8400-...
2026-01-30 11:22:34 - corporate_api - WARNING - [request_id=def-456] - Project not whitelisted: message_id=..., project=XXX
```

## Reverse Proxy Configuration

These services assume mTLS is enforced by a reverse proxy. Example Nginx configuration:

```nginx
server {
    listen 443 ssl;

    ssl_certificate /etc/ssl/server.crt;
    ssl_certificate_key /etc/ssl/server.key;
    ssl_client_certificate /etc/ssl/ca.crt;
    ssl_verify_client on;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Client-Cert-DN $ssl_client_s_dn;
        proxy_set_header X-Client-Cert-Fingerprint $ssl_client_fingerprint;
    }

    # Restrict /dmz/messages to Gateway certificates only
    location /dmz/messages {
        if ($ssl_client_s_dn !~ "CN=gateway.dmz.example.com") {
            return 403;
        }
        proxy_pass http://127.0.0.1:8000;
    }
}
```

## License

Internal use only.


## Certificate Gateway (JWT Wrapping)

Before messages are sent to the DMZ Gateway, the corporate (high-side) service sends them to an external **Certificate Gateway** to be wrapped with a signed JWT token.

### Flow

```
Message → Cert Gateway (/wrap) → JWT-wrapped message → DMZ Gateway (/messages)
```

### Configuration

Set `CERT_GATEWAY_URL` in environment or `config.json`:

```bash
export CERT_GATEWAY_URL=https://cert-gateway.your-org.com
```

Default placeholder: `https://cert-gateway.example.com`

The cert gateway is expected to expose a `POST /wrap` endpoint that accepts a message JSON body and returns:

```json
{
  "token": "<signed JWT>",
  "expires_at": "2026-04-08T14:30:00",
  "message": { ... }
}
```

### Auto-Send

When sending messages from the user portal, an **Auto-send** checkbox (checked by default) controls whether the message is forwarded to the DMZ Gateway immediately after cert wrapping, or held for manual release later.

## Error Storage

All application errors (gateway failures, file write errors, etc.) are stored as JSON files in `./data/errors/` on both corporate and low-side services.

Error files are named `{error_id}.json` and contain:

```json
{
  "error_id": "uuid",
  "timestamp": "2026-04-07T10:30:00",
  "error_type": "gateway_unavailable",
  "message": "Gateway unavailable after 3 attempts",
  "message_id": "uuid",
  "request_id": "uuid"
}
```

Configure the error directory:

```bash
export ERROR_DIR=./data/errors
```

## Cleanup Script

`cleanup.sh` removes JSON files older than 1 day from all data directories (messages, tmp, errors) on both services.

```bash
# Preview what would be deleted
./cleanup.sh --dry-run

# Run cleanup
./cleanup.sh
```

### Cron Job Setup

Schedule the cleanup to run daily (e.g., at 2:00 AM):

```bash
# Edit crontab
crontab -e

# Add this line (adjust path to your installation):
0 2 * * * /path/to/API-DMZ-API/cleanup.sh >> /var/log/dmz-cleanup.log 2>&1
```

For more frequent cleanup (e.g., every 6 hours):

```bash
0 */6 * * * /path/to/API-DMZ-API/cleanup.sh >> /var/log/dmz-cleanup.log 2>&1
```

Verify your cron entry:

```bash
crontab -l
```

## Full Setup Instructions

### Prerequisites

- Python 3.10+
- pip
- (Production) Nginx/Envoy reverse proxy with mTLS certificates
- Access to the Certificate Gateway service (URL from your infrastructure team)

### Quick Start (Development)

```bash
# 1. Clone the repository
git clone <repo-url>
cd API-DMZ-API

# 2. Install dependencies for all services
install_deps.bat          # Windows
# or manually:
pip install -r corporate/requirements.txt
pip install -r low_side/requirements.txt
pip install -r mock_gateway/requirements.txt

# 3. Start all services
start_services.bat        # Windows (starts 3 terminal windows)
# or manually:
cd corporate   && python -m uvicorn app.main:app --port 8001 --reload &
cd low_side    && python -m uvicorn app.main:app --port 8002 --reload &
cd mock_gateway && python -m uvicorn main:app    --port 8000 --reload &

# 4. Access the services
# Corporate Admin:       http://localhost:8001/admin/  (admin/admin123)
# Corporate User Portal: http://localhost:8001/user/
# Low-Side User Portal:  http://localhost:8002/user/
# Mock Gateway:          http://localhost:8000/docs
# API Docs:              http://localhost:8001/docs, http://localhost:8002/docs
```

### Production Deployment

1. **Set environment variables** (or create `config.json` in each service directory):

   ```bash
   # Corporate
   export GATEWAY_URL=https://gateway.dmz.example.com
   export CERT_GATEWAY_URL=https://cert-gateway.your-org.com
   export ADMIN_PASSWORD=<strong-password>
   export MASTER_DIR=/var/data/corporate/messages
   export TMP_DIR=/var/data/corporate/tmp
   export ERROR_DIR=/var/data/corporate/errors
   export USERS_FILE_PATH=/var/data/corporate/users.json
   export WHITELIST_FILE_PATH=/var/data/corporate/whitelist.json

   # Low-side
   export GATEWAY_URL=https://gateway.dmz.example.com
   export MASTER_DIR=/var/data/lowside/messages
   export TMP_DIR=/var/data/lowside/tmp
   export ERROR_DIR=/var/data/lowside/errors
   export USERS_FILE_PATH=/var/data/lowside/users.json
   ```

2. **Configure reverse proxy** with mTLS (see Reverse Proxy Configuration section above)

3. **Set up cron job** for cleanup:

   ```bash
   0 2 * * * /opt/dmz-api/cleanup.sh >> /var/log/dmz-cleanup.log 2>&1
   ```

4. **Initialize whitelist** (corporate only):

   ```bash
   cd corporate
   python scripts/whitelist_admin.py add AAA
   python scripts/whitelist_admin.py add BBB
   ```

5. **Start services** via systemd, supervisor, or your preferred process manager.

## Key Management & mTLS Client Certificates (Admin)

The admin **Keys** page (`/admin/keys`) generates RSA key pairs **and** issues a signed client certificate for each one. The app acts as its own Certificate Authority — every client cert is signed by the app's internal CA, which is created automatically the first time you generate a key.

### Files per key pair

Stored in `./data/keys/{key_id}/`:

| File | Purpose |
|------|---------|
| `private.pem` | Client private key (**keep secret**) |
| `public.pem` | Client public key |
| `client.crt` | X.509 client certificate signed by the app's CA |
| `client.pfx` | PKCS#12 bundle (cert + key + CA chain), password `changeme` |
| `metadata.json` | Name, size, created/expiry dates, status |

The CA lives in `./data/keys/_ca/` (`ca.key` + `ca.crt`) — generated on first use and reused for all subsequent client certs.

### Workflow

1. Log in as admin → **Keys** tab (`/admin/keys`)
2. Click **Download CA Certificate (ca.crt)** — this is what the server will trust
3. Click **Generate Key** with a name (e.g. `postman-dev`, `script-runner`)
4. Download files via the buttons on each row:
   - **cert** → `client.crt`
   - **key** → `private.pem`
   - **pfx** → `client.pfx` (for Postman/browsers)
5. Use these when calling the API (see below)

### Running the API with mTLS

Start uvicorn with TLS and client cert verification:

```bash
cd corporate
python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile ./data/server/server.key \
  --ssl-certfile ./data/server/server.crt \
  --ssl-ca-certs ./data/keys/_ca/ca.crt \
  --ssl-cert-reqs 2
```

| Flag | Meaning |
|------|---------|
| `--ssl-keyfile` / `--ssl-certfile` | Server's own cert (generate separately, or use your existing PKI) |
| `--ssl-ca-certs` | Trust anchor — accepts any client cert signed by this CA |
| `--ssl-cert-reqs 2` | Require a valid client cert (`CERT_REQUIRED`). Use `1` for optional, `0` for none |

Once started, the API is reachable at `https://localhost:8443` **only** with a valid client cert.

> **Note**: You still need a server cert/key. If you don't have one, run `certs/generate_certs.ps1` or `certs/generate_certs.sh` (included in this repo) to produce a `server.crt`/`server.key` signed by a local CA. For production, use your corporate PKI or Let's Encrypt for the server cert.

### Making API Calls with Your Client Cert

#### curl
```bash
curl --cert client.crt --key private.pem --cacert ca.crt \
     https://localhost:8443/health
```

Post a message:
```bash
curl --cert client.crt --key private.pem --cacert ca.crt \
     -X POST https://localhost:8443/messages \
     -H "Content-Type: application/json" \
     -d '{
       "ID": "550e8400-e29b-41d4-a716-446655440000",
       "Project": "AAA",
       "TestID": "TST001",
       "Area": "Integration",
       "Date": "2026-01-30T11:22:33",
       "Status": "Inprogress",
       "Data": {"result": "pass"}
     }'
```

#### Python (httpx)
```python
import httpx

client = httpx.Client(
    cert=("client.crt", "private.pem"),
    verify="ca.crt",
)
r = client.get("https://localhost:8443/health")
print(r.status_code, r.json())
```

#### Python (requests)
```python
import requests

r = requests.get(
    "https://localhost:8443/health",
    cert=("client.crt", "private.pem"),
    verify="ca.crt",
)
print(r.status_code, r.json())
```

#### PowerShell (Invoke-RestMethod)
```powershell
# Requires PowerShell 7+
$cert = Get-PfxCertificate -FilePath client.pfx
Invoke-RestMethod -Uri https://localhost:8443/health -Certificate $cert
```

#### Postman
1. **Settings → Certificates → Add Certificate**
2. Host: `localhost:8443`
3. CRT file: `client.crt`
4. KEY file: `private.pem`
5. (Optional) **CA Certificates** — load `ca.crt` so Postman trusts the server
6. Send requests normally — Postman attaches the client cert automatically

Alternative: use the PFX bundle directly. Postman also accepts `.pfx` with the password (default `changeme`).

#### Browser
Import `client.pfx` (password `changeme`) into your OS/browser cert store:
- **Windows**: double-click the `.pfx` → follow import wizard → "Personal" store
- **Mac**: double-click → Keychain Access → System or Login keychain
- **Firefox**: Settings → Privacy & Security → Certificates → View Certificates → Your Certificates → Import

The browser will prompt you to pick a cert when connecting to `https://localhost:8443`.

### Verifying mTLS is Enforced

Without a client cert, the TLS handshake fails:

```bash
curl --cacert ca.crt https://localhost:8443/health
# curl: (35) error:0A000410:SSL routines::sslv3 alert handshake failure
```

That's the server rejecting you — exactly what you want.

### Admin Actions

- **View Public Key** — displays the PEM public key on screen (safe to share)
- **Revoke** — marks the key as revoked in metadata. Note: revoked certs are **not** yet enforced by the server; you'd need to distribute a CRL or switch to OCSP for true revocation
- **Delete** — permanently removes the key pair directory

### Security Notes

- **Never commit `private.pem` or `.pfx` files** — treat them like passwords
- **Rotate certs** — default validity is 365 days. Generate a new pair and delete the old one
- **CA private key** (`./data/keys/_ca/ca.key`) — anyone with this can mint valid client certs. Back it up securely, restrict filesystem access to the service account
- **PFX password** (`changeme`) — change `DEFAULT_PFX_PASSWORD` in `key_manager.py` before using in anger
- **Production** — consider using a real enterprise CA rather than the app's self-signed CA. The app can still issue keys, but you'd sign them with your corporate CA instead

## Message History (User Portal)

The **History** page (`/user/history`) displays all received messages with:

- **Project filter** dropdown
- **Text search** across Message ID, Test ID, Test Status
- Color-coded status badges (complete/fail/in-progress)
- Data key summary column

## Pending Message Queue (User Portal)

When a message is sent with the **Auto-send** checkbox unchecked, it is cert-wrapped and saved to the **Pending** queue (`/user/pending`).

- View all queued messages with cert expiry times
- **Send Now** — release a pending message to the gateway
- **Discard** — permanently remove from the queue

Pending messages are stored in `./data/pending/{message_id}.json`.

# CI/CD Lifecycle and creation 

Mocks that need creating - mock to create the wrapper JWT cert
Mock Gateway 
Update Configuration to point at the Gateway

## Pipeline stages

1. Prepare & Setup
2. Linting & Validation
3. Unit Tests
4. Build
5. Static Analysis
6. Dependency Analysis
7. Integration Tests
8. Package / Publish

## Prepare & Setup
### Purpose
- Set up environment
- validate pipeline inputs
- Restore caches

## Minimum requirements

- validate configuration files
- Load secrets securely
- Configure caches

## Linting & Validation 
- Code linting
- Formatting checks
- Configuration validation

## Unit Test
### Purpose

- run on every Commit
- fast and isolated
- No external dependencies

## Build Stage 
- Compile or package the application
- Produce deterministic build artifacts

### Requirements
- Builds must be reproducible
- Dependencies version must be pinned
- Build artifacts must be stored as pipeline artifacts

## Static Analysis Stage 
### Purpose
- Detect code smells, bugs and maintainability issues

### Requirements
- Must run on every merge request
- Must block merge on critical issues
- Results must be visible n Gitlab

SonarQube (preferred Example)
 - Use SonarQube or equivalent
 - enforce quality gates

## Dependency Analysis

- Identify vulnerable or con- compliant dependencies
- Support OSS governance and licence compliance

### Python 
 - pip-audit
 - safety
 - Lock file validation

### Node.js
- npm audit /yarn audit
- Lock file enforcement

### Requirements
- High and Critical vulnerabilities must fail the pipeline
- Accepted risks must be documented

## Testing Stage
### Integration Tests
 - Validate component interation
 - May use test containers or mocks
 - Run at least on merge requests

### System / End-to-End Tests
 - Validate full system behaviour
 - May run less frequently due to cost
 - Required before production deployment

### Code Coverage 
 - Coverage must be reported
 - Minimum thresholds should be defined per project
 - Drops in coverage must be visible

## Package / Publish stage

- Publish build artifacts or Images

### requirements
- Version artifacts consistently
- Never overwrite release versions

## GitLab CI Best Practices
- .gitlab-ci.yml is version controlled
- Changes reviewed via merge requests

### Resusable Pipelines
 - use include and templates
 - avoid copy-past across repositories
 
### Secret Management
- Never store secrets in git
- Use GitLab CI variables or secret managers
- Mask and protect sensitive varables

# Updating Service Addresses
There are three ways to set addresses, in priority order (highest wins):

Option 1: Environment variables (recommended for deployment)
Set these before starting the service:

Windows (cmd):


set GATEWAY_URL=https://gateway.prod.example.com
set CERT_GATEWAY_URL=https://cert-gateway.prod.example.com:8443
python -m uvicorn app.main:app --port 8001
Windows (PowerShell):


$env:GATEWAY_URL="https://gateway.prod.example.com"
$env:CERT_GATEWAY_URL="https://cert-gateway.prod.example.com:8443"
python -m uvicorn app.main:app --port 8001
Linux/Mac:


export GATEWAY_URL=https://gateway.prod.example.com
export CERT_GATEWAY_URL=https://cert-gateway.prod.example.com:8443
python -m uvicorn app.main:app --port 8001
Option 2: config.json (recommended for persistent config)
Create corporate/config.json:


{
  "GATEWAY_URL": "https://gateway.prod.example.com",
  "CERT_GATEWAY_URL": "https://cert-gateway.prod.example.com:8443",
  "MASTER_DIR": "/var/data/corporate/messages",
  "ERROR_DIR": "/var/data/corporate/errors"
}
Create low_side/config.json:


{
  "GATEWAY_URL": "https://gateway.prod.example.com",
  "MASTER_DIR": "/var/data/lowside/messages"
}
Option 3: Edit defaults in config.py (development only)
Corporate cert + gateway: corporate/app/config.py lines 42, 45
Low-side gateway: low_side/app/config.py line 37
What each variable means
Variable	Used by	Description	Current default
GATEWAY_URL	corporate, low-side	DMZ Gateway base URL	http://localhost:8000
CERT_GATEWAY_URL	corporate only	Certificate gateway for JWT wrapping	https://cert-gateway.example.com
LOW_SIDE_URL	mock gateway	Where mock gateway forwards to low-side	http://localhost:8002
CORPORATE_URL	mock gateway	Where mock gateway forwards to corporate	http://localhost:8001
Updating for a real estate
For the corporate side talking to your real cert gateway:


// corporate/config.json
{
  "GATEWAY_URL": "https://dmz-gateway.internal.yourorg.com",
  "CERT_GATEWAY_URL": "https://cert-wrapper.internal.yourorg.com/api/v1"
}
The corporate service will POST to {CERT_GATEWAY_URL}/wrap for JWT wrapping, then POST the wrapped payload to {GATEWAY_URL}/messages.

Listener address/port
The service's own listener (what IP/port it binds to) is set on the uvicorn command line, not in config:


## Listen on all interfaces, port 8001
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

## Listen on specific IP
python -m uvicorn app.main:app --host 10.0.1.25 --port 8001

## Loopback only (default)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
Verifying the config took effect
After starting a service, check the startup logs — both services log the resolved URLs:


INFO - Message store: /var/data/corporate/messages
INFO - Gateway URL: https://dmz-gateway.internal.yourorg.com
INFO - Whitelist file: /var/data/corporate/whitelist.json
Restarting
Config changes require a restart of the service. The one exception is the whitelist (whitelist.json) which auto-reloads when the file's mtime changes — no restart needed for whitelist edits.

add these too README.md
Grep "^## Configuration" (in h:\repos\API-DMZ-API\README.md)
1 line of output
Read h:\repos\API-DMZ-API\README.md (lines 171-195)
I'll replace the existing stale Configuration section with the comprehensive instructions:

Edit h:\repos\API-DMZ-API\README.md
Added 94 lines
Added. The README's Configuration section now has complete instructions covering:

Three config methods (env vars, config.json, defaults) with priority order
Full env var table including GATEWAY_URL, CERT_GATEWAY_URL, MASTER_DIR, ERROR_DIR, KEYS_DIR, PENDING_DIR, etc.
Per-OS examples (Windows cmd, PowerShell, Linux/Mac)
config.json examples for both corporate and low-side
Listener address/port explanation (uvicorn CLI flags)
Verification — how to confirm the config took effect via startup logs
Restart requirements — noting that whitelist auto-reloads but everything else needs a restart
Message flow diagram showing where each URL is used