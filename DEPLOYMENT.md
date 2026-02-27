# DMZ API — Deployment & Architecture Guide

## Table of Contents

1. [System Overview](#system-overview)
2. [Service Architecture](#service-architecture)
3. [Message Flow](#message-flow)
4. [User Sync Flow](#user-sync-flow)
5. [Certificate Architecture](#certificate-architecture)
6. [Generating Certificates](#generating-certificates)
7. [Nginx Configuration](#nginx-configuration)
8. [GitLab CI/CD Integration](#gitlab-cicd-integration)
9. [Configuration Reference](#configuration-reference)
10. [Running the Services](#running-the-services)

---

## System Overview

This system provides a secure, auditable message channel between a **corporate network** and a
**low-side (restricted) network**, connected through a **DMZ Gateway**.

Key security principles:
- The corporate and low-side networks are **physically or logically isolated**
- The only communication path between them is through the **DMZ Gateway**
- mTLS (mutual TLS) is enforced at the reverse proxy level on each side
- Each side has its **own Certificate Authority** — corporate certs are never seen on the low-side
- User accounts are created on corporate and **pushed through the gateway** to the low-side
- The gateway acts as the single break point with two separate identities

---

## Service Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  CORPORATE NETWORK                                                  │
│                                                                     │
│  ┌─────────────────────────────────┐                               │
│  │  Corporate API  (port 8001)     │                               │
│  │  - Admin web UI  /admin/        │                               │
│  │  - User portal   /user/         │                               │
│  │  - Send messages POST /messages │                               │
│  │  - Receive inbound  /dmz/       │                               │
│  └──────────────┬──────────────────┘                               │
│                 │  mTLS (Corp CA)                                   │
└─────────────────┼───────────────────────────────────────────────────┘
                  │
┌─────────────────┼───────────────────────────────────────────────────┐
│  DMZ            │                                                   │
│                 ▼                                                   │
│  ┌─────────────────────────────────┐                               │
│  │  Gateway  (port 8000)           │                               │
│  │  - Receives POST /messages      │                               │
│  │  - Forwards to both sides       │                               │
│  │  - Receives POST /users         │                               │
│  │  - Forwards user sync to low    │                               │
│  │  - Two certs: Corp + Low        │                               │
│  └──────────────┬──────────────────┘                               │
│                 │  mTLS (Low-Side CA)                               │
└─────────────────┼───────────────────────────────────────────────────┘
                  │
┌─────────────────┼───────────────────────────────────────────────────┐
│  LOW-SIDE NETWORK                                                   │
│                 ▼                                                   │
│  ┌─────────────────────────────────┐                               │
│  │  Low-Side API  (port 8002)      │                               │
│  │  - User portal  /user/          │                               │
│  │  - Send messages POST /messages │                               │
│  │  - Receive inbound  /dmz/       │                               │
│  │  - Receive user sync /dmz/users │                               │
│  └─────────────────────────────────┘                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Mock Gateway | http://localhost:8000 | DMZ relay (testing) |
| Corporate Admin | http://localhost:8001/admin/ | Manage projects, users |
| Corporate User Portal | http://localhost:8001/user/ | Send messages, change password |
| Corporate API Docs | http://localhost:8001/docs | OpenAPI documentation |
| Low-Side User Portal | http://localhost:8002/user/ | Send messages, change password |
| Low-Side API Docs | http://localhost:8002/docs | OpenAPI documentation |

---

## Message Flow

### Corporate → Low-Side

```
1. Corporate user fills in message form at /user/send
2. Corporate API validates message schema + project whitelist
3. Corporate API POSTs to gateway  POST /messages
4. Gateway saves to ./received/ for inspection
5. Gateway forwards to low-side   POST /dmz/messages
6. Low-side validates schema and writes to disk
   → low_side/data/messages/{Project}/{id}.json
```

### Low-Side → Corporate

```
1. Low-side user fills in message form at /user/send
2. Low-side API validates message schema
3. Low-side API POSTs to gateway  POST /messages
4. Gateway saves to ./received/ for inspection
5. Gateway forwards to corporate  POST /dmz/messages
6. Corporate validates schema + project whitelist and writes to disk
   → corporate/data/messages/{Project}/{id}.json
```

### Message Schema

All messages must conform to this schema:

```json
{
  "ID":          "550e8400-e29b-41d4-a716-446655440000",
  "Project":     "ABC",
  "Test ID":     "TEST001",
  "Timestamp":   "2026-01-30T11:22:33",
  "Test Status": "Pass",
  "Data": {
    "result": "pass",
    "note":   "all checks passed"
  }
}
```

| Field | Format | Rules |
|-------|--------|-------|
| `ID` | UUID | Valid UUID v4 |
| `Project` | String | Exactly 3 chars, A-Z 0-9, must be whitelisted on corporate |
| `Test ID` | String | 3–10 characters |
| `Timestamp` | ISO 8601 | e.g. `2026-01-30T11:22:33` |
| `Test Status` | String | Any string |
| `Data` | Object | Max 20 string key-value pairs, values 1–128 chars (a-z A-Z 0-9 space ;) |

---

## User Sync Flow

User accounts are created by the corporate administrator and automatically synced to the
low-side through the gateway. The low-side never connects to corporate directly.

```
1. Admin creates user at corporate /admin/users
2. Corporate calls gateway  POST /users  with:
   {
     "username":             "testuser",
     "action":               "upsert",          // or "delete"
     "password_hash":        "salt:hash",
     "enabled":              true,
     "must_change_password": true
   }
3. Gateway forwards to low-side  POST /dmz/users
4. Low-side stores in  low_side/data/users.json
5. User can now log in at  http://low-side:8002/user/

Sync is triggered on: create, enable, disable, delete, password reset
```

On first login, the user is forced to change their password. The new password is stored
only on that side — it is **not** synced back to corporate.

---

## Certificate Architecture

Each network has its own Certificate Authority (CA). Corporate certs are **never present**
on the low-side and vice versa.

```
CORPORATE SIDE                    DMZ GATEWAY               LOW-SIDE
──────────────                    ───────────               ─────────
corp-ca.crt/key                                             low-ca.crt/key
corp-server.crt   ←── Nginx ──►  gateway-corp-client.crt
                                  gateway-low-client.crt  ←── Nginx ──►  low-server.crt
```

### Certificate Roles

| Certificate | Signed By | Used By | Purpose |
|-------------|-----------|---------|---------|
| `corp-ca.crt` | Self | Corporate Nginx | Trust anchor for corporate side |
| `corp-server.crt` | Corp CA | Corporate Nginx | Identifies the corporate API server |
| `gateway-corp-client.crt` | Corp CA | Gateway | Gateway proves identity to corporate |
| `low-ca.crt` | Self | Low-Side Nginx | Trust anchor for low-side |
| `low-server.crt` | Low CA | Low-Side Nginx | Identifies the low-side API server |
| `gateway-low-client.crt` | Low CA | Gateway | Gateway proves identity to low-side |

### Security Properties

- Low-side Nginx only trusts `low-ca.crt` — any request with a corporate certificate is **rejected**
- Corporate Nginx only trusts `corp-ca.crt` — any request with a low-side certificate is **rejected**
- The gateway holds one cert from each CA — it is the only entity that can communicate with both sides
- If the gateway is compromised it cannot present forged certs because it does not hold either CA private key

---

## Generating Certificates

### Corporate Side Certificates

```bash
# Corporate CA
openssl genrsa -out corp-ca.key 4096
openssl req -new -x509 -days 1826 -key corp-ca.key -out corp-ca.crt \
  -subj "/CN=Corporate-CA/O=YourOrg"

# Corporate API server cert
openssl genrsa -out corp-server.key 2048
openssl req -new -key corp-server.key -out corp-server.csr \
  -subj "/CN=corporate-api.yourorg.internal/O=YourOrg"
openssl x509 -req -days 365 \
  -in corp-server.csr -CA corp-ca.crt -CAkey corp-ca.key \
  -CAcreateserial -out corp-server.crt

# Gateway client cert for corporate side (gateway uses this to talk to corporate)
openssl genrsa -out gateway-corp-client.key 2048
openssl req -new -key gateway-corp-client.key -out gateway-corp-client.csr \
  -subj "/CN=dmz-gateway/O=YourOrg"
openssl x509 -req -days 365 \
  -in gateway-corp-client.csr -CA corp-ca.crt -CAkey corp-ca.key \
  -CAcreateserial -out gateway-corp-client.crt
```

### Low-Side Certificates

```bash
# Low-Side CA  (kept entirely on the low-side, never shared with corporate)
openssl genrsa -out low-ca.key 4096
openssl req -new -x509 -days 1826 -key low-ca.key -out low-ca.crt \
  -subj "/CN=LowSide-CA/O=YourOrg"

# Low-Side API server cert
openssl genrsa -out low-server.key 2048
openssl req -new -key low-server.key -out low-server.csr \
  -subj "/CN=low-side-api.yourorg.internal/O=YourOrg"
openssl x509 -req -days 365 \
  -in low-server.csr -CA low-ca.crt -CAkey low-ca.key \
  -CAcreateserial -out low-server.crt

# Gateway client cert for low-side (gateway uses this to talk to low-side)
openssl genrsa -out gateway-low-client.key 2048
openssl req -new -key gateway-low-client.key -out gateway-low-client.csr \
  -subj "/CN=dmz-gateway/O=YourOrg"
openssl x509 -req -days 365 \
  -in gateway-low-client.csr -CA low-ca.crt -CAkey low-ca.key \
  -CAcreateserial -out gateway-low-client.crt
```

### Certificate Distribution

| File | Goes to |
|------|---------|
| `corp-ca.crt` | Corporate Nginx, Gateway (to validate corp server) |
| `corp-server.crt` + `corp-server.key` | Corporate Nginx |
| `gateway-corp-client.crt` + `.key` | Gateway only |
| `low-ca.crt` | Low-Side Nginx, Gateway (to validate low server) |
| `low-server.crt` + `low-server.key` | Low-Side Nginx |
| `gateway-low-client.crt` + `.key` | Gateway only |
| `corp-ca.key` | **Corporate CA only — never leaves corporate** |
| `low-ca.key` | **Low-Side CA only — never leaves low-side** |

---

## Nginx Configuration

### Corporate Side Nginx

```nginx
server {
    listen 443 ssl;
    server_name corporate-api.yourorg.internal;

    ssl_certificate     /etc/certs/corp-server.crt;
    ssl_certificate_key /etc/certs/corp-server.key;

    # Only trust the Corporate CA — low-side certs are rejected
    ssl_client_certificate /etc/certs/corp-ca.crt;
    ssl_verify_client on;

    # DMZ endpoints — only gateway can reach these (gateway presents corp client cert)
    location /dmz/ {
        proxy_set_header X-Client-Cert-DN          $ssl_client_s_dn;
        proxy_set_header X-Client-Cert-Fingerprint $ssl_client_fingerprint;
        proxy_set_header X-Forwarded-For           $remote_addr;
        proxy_pass http://localhost:8001;
    }

    # Admin and user portal — internal users with corp cert
    location / {
        proxy_set_header X-Client-Cert-DN $ssl_client_s_dn;
        proxy_pass http://localhost:8001;
    }
}
```

### Low-Side Nginx

```nginx
server {
    listen 443 ssl;
    server_name low-side-api.yourorg.internal;

    ssl_certificate     /etc/certs/low-server.crt;
    ssl_certificate_key /etc/certs/low-server.key;

    # Only trust the Low-Side CA — corporate certs are rejected
    ssl_client_certificate /etc/certs/low-ca.crt;
    ssl_verify_client on;

    # DMZ endpoints — only gateway can reach these (gateway presents low client cert)
    location /dmz/ {
        proxy_set_header X-Client-Cert-DN          $ssl_client_s_dn;
        proxy_set_header X-Client-Cert-Fingerprint $ssl_client_fingerprint;
        proxy_set_header X-Forwarded-For           $remote_addr;
        proxy_pass http://localhost:8002;
    }

    # User portal — low-side users with low cert
    location / {
        proxy_set_header X-Client-Cert-DN $ssl_client_s_dn;
        proxy_pass http://localhost:8002;
    }
}
```

### Gateway Nginx (optional, for TLS termination at the gateway)

```nginx
# Corporate-facing side of the gateway
server {
    listen 8443 ssl;

    ssl_certificate     /etc/certs/gateway-corp-client.crt;
    ssl_certificate_key /etc/certs/gateway-corp-client.key;
    ssl_client_certificate /etc/certs/corp-ca.crt;
    ssl_verify_client on;

    location / {
        proxy_pass http://localhost:8000;
    }
}
```

---

## GitLab CI/CD Integration

You can send test results automatically from a GitLab pipeline to the corporate API.

### Basic Example — `.gitlab-ci.yml`

```yaml
stages:
  - test
  - notify

run-tests:
  stage: test
  script:
    - python -m pytest --junitxml=results.xml
  artifacts:
    reports:
      junit: results.xml

send-results:
  stage: notify
  needs: [run-tests]
  when: always          # runs even if tests fail
  variables:
    API_URL: "https://corporate-api.yourorg.internal"
    PROJECT_CODE: "ABC"
  script:
    - |
      STATUS="Pass"
      if [ "$CI_JOB_STATUS" = "failed" ]; then STATUS="Fail"; fi

      UUID=$(python3 -c "import uuid; print(uuid.uuid4())")
      TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S)

      curl -s -X POST "${API_URL}/messages" \
        -H "Content-Type: application/json" \
        --cert /etc/certs/gateway-corp-client.crt \
        --key  /etc/certs/gateway-corp-client.key \
        --cacert /etc/certs/corp-ca.crt \
        -d "{
          \"ID\":          \"${UUID}\",
          \"Project\":     \"${PROJECT_CODE}\",
          \"Test ID\":     \"${CI_PIPELINE_ID}\",
          \"Timestamp\":   \"${TIMESTAMP}\",
          \"Test Status\": \"${STATUS}\",
          \"Data\": {
            \"pipeline\": \"${CI_PIPELINE_ID}\",
            \"branch\":   \"${CI_COMMIT_BRANCH}\",
            \"commit\":   \"${CI_COMMIT_SHORT_SHA}\",
            \"repo\":     \"${CI_PROJECT_NAME}\"
          }
        }"
```

### Useful GitLab CI Variables

| Variable | Example Value | Description |
|----------|---------------|-------------|
| `$CI_PIPELINE_ID` | `12345` | Unique pipeline number |
| `$CI_JOB_STATUS` | `success` / `failed` | Outcome of the job |
| `$CI_COMMIT_BRANCH` | `main` | Branch that triggered the pipeline |
| `$CI_COMMIT_SHORT_SHA` | `a1b2c3d` | Short commit hash |
| `$CI_PROJECT_NAME` | `my-repo` | Repository name |
| `$CI_ENVIRONMENT_NAME` | `production` | Deployment environment (if set) |

### Storing the Client Certificate in GitLab

Store the cert and key as **CI/CD file variables** (not masked variables) in
**GitLab → Settings → CI/CD → Variables**:

| Variable Name | Type | Value |
|---------------|------|-------|
| `API_CLIENT_CERT` | File | Contents of `gateway-corp-client.crt` |
| `API_CLIENT_KEY` | File | Contents of `gateway-corp-client.key` |
| `API_CA_CERT` | File | Contents of `corp-ca.crt` |

Then reference them in the script:

```yaml
script:
  - |
    curl -X POST "${API_URL}/messages" \
      --cert  "$API_CLIENT_CERT" \
      --key   "$API_CLIENT_KEY" \
      --cacert "$API_CA_CERT" \
      ...
```

---

## Configuration Reference

### Corporate API — `corporate/config.json`

```json
{
  "COMPANY_NAME":      "Acme Corp",
  "SERVICE_NAME":      "DMZ Gateway",
  "NETWORK_LABEL":     "CORPORATE NETWORK",
  "ADMIN_PASSWORD":    "change-me-in-production",
  "GATEWAY_URL":       "https://gateway.yourorg.dmz:8000",
  "MASTER_DIR":        "./data/messages",
  "TMP_DIR":           "./data/tmp",
  "WHITELIST_FILE_PATH": "./data/whitelist.json",
  "USERS_FILE_PATH":   "./data/users.json"
}
```

### Low-Side API — `low_side/config.json`

```json
{
  "GATEWAY_URL":     "https://gateway.yourorg.dmz:8000",
  "MASTER_DIR":      "./data/messages",
  "TMP_DIR":         "./data/tmp",
  "USERS_FILE_PATH": "./data/users.json"
}
```

### Mock Gateway — Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOW_SIDE_URL` | `http://localhost:8002` | Low-side API URL for forwarding |
| `CORPORATE_URL` | `http://localhost:8001` | Corporate API URL for forwarding |

---

## Running the Services

### Windows (testing)

```bat
install_deps.bat     # install all Python dependencies (run once)
start_services.bat   # open three service windows
```

### Linux / Production

```bash
# Corporate API
cd corporate
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Low-Side API
cd low_side
uvicorn app.main:app --host 0.0.0.0 --port 8002

# Gateway (testing only — replace with real gateway in production)
cd mock_gateway
uvicorn main:app --host 0.0.0.0 --port 8000
```

### File Locations After Running

| Location | Contents |
|----------|----------|
| `mock_gateway/received/` | All messages received by gateway |
| `corporate/data/messages/{Project}/` | Messages stored by corporate |
| `low_side/data/messages/{Project}/` | Messages stored by low-side |
| `corporate/data/whitelist.json` | Authorised project codes |
| `corporate/data/users.json` | User accounts (corporate) |
| `low_side/data/users.json` | User accounts synced from corporate |

### Default Login

| Portal | URL | Username | Password |
|--------|-----|----------|----------|
| Corporate Admin | /admin/ | admin | admin123 |

> **Change the default admin password immediately.** Set `ADMIN_PASSWORD` in `config.json`
> or as an environment variable before deploying.
