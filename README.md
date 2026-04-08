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

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `./data` | Base directory for file storage |
| `GATEWAY_URL` | `http://localhost:8080` | DMZ Gateway base URL |
| `WHITELIST_FILE_PATH` | `./data/whitelist.json` | Whitelist JSON file path (corporate only) |

### Example

```bash
export DATA_DIR=/var/data/dmz
export GATEWAY_URL=https://gateway.dmz.example.com
export WHITELIST_FILE_PATH=/var/data/whitelist.json

uvicorn app.main:app --host 0.0.0.0 --port 8000
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

## Key Management (Admin)

The admin panel includes a **Keys** page (`/admin/keys`) for generating and managing RSA key pairs.

- **Generate**: Create RSA 2048-bit or 4096-bit key pairs (requires `cryptography` package)
- **View Public Key**: Copy the PEM public key to share with the cert gateway or partners
- **Revoke**: Mark a key as revoked (does not delete files)
- **Delete**: Permanently remove a key pair

Keys are stored in `./data/keys/{key_id}/` with `private.pem`, `public.pem`, and `metadata.json`.

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