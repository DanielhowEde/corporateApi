#!/bin/bash
# =============================================================================
# Generate mTLS Certificates for DMZ API (Linux/Mac)
# =============================================================================

set -euo pipefail
CERT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$CERT_DIR"

CA_CN="DMZ-API Internal CA"
SERVER_CN="localhost"
CLIENT_CN="api-client"
PFX_PASSWORD="changeme"
DAYS=365

echo "======================================"
echo " Generating mTLS certificates"
echo " Output: $CERT_DIR"
echo "======================================"

# 1. CA
echo "[1/4] Creating Certificate Authority..."
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "/CN=$CA_CN/O=DMZ-API" -out ca.crt

# 2. Server cert
echo "[2/4] Creating Server certificate..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=$SERVER_CN/O=DMZ-API" -out server.csr

cat > server.ext <<EOF
subjectAltName = DNS:localhost,DNS:127.0.0.1,IP:127.0.0.1,IP:::1
extendedKeyUsage = serverAuth
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days "$DAYS" -sha256 -extfile server.ext
rm -f server.csr server.ext

# 3. Client cert
echo "[3/4] Creating Client certificate..."
openssl genrsa -out client.key 2048
openssl req -new -key client.key -subj "/CN=$CLIENT_CN/O=DMZ-API" -out client.csr

cat > client.ext <<EOF
extendedKeyUsage = clientAuth
EOF

openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out client.crt -days "$DAYS" -sha256 -extfile client.ext
rm -f client.csr client.ext

# 4. PFX bundle
echo "[4/4] Creating client.pfx bundle..."
openssl pkcs12 -export -out client.pfx \
    -inkey client.key -in client.crt -certfile ca.crt \
    -password "pass:$PFX_PASSWORD"

echo ""
echo "======================================"
echo " Done. Files in $CERT_DIR:"
echo "   ca.crt         - trust this on the server"
echo "   server.crt/key - serve with uvicorn"
echo "   client.crt/key - present when calling API"
echo "   client.pfx     - for Postman/browser (password: $PFX_PASSWORD)"
echo "======================================"
