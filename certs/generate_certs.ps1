# =============================================================================
# Generate mTLS Certificates for DMZ API
# =============================================================================
#
# Creates:
#   ca.key           - Certificate Authority private key
#   ca.crt           - Certificate Authority public cert (used by server to verify clients)
#   server.key       - Server private key
#   server.crt       - Server public cert (signed by CA)
#   client.key       - Client private key
#   client.crt       - Client public cert (signed by CA)
#   client.pfx       - Combined client cert+key (for Windows / Postman / browsers)
#
# Requires: OpenSSL installed and on PATH
#   Install via: winget install OpenSSL (or choco install openssl)
# =============================================================================

$ErrorActionPreference = "Stop"
$CertDir = $PSScriptRoot

# Common Name defaults - change these for your environment
$CACommonName      = "DMZ-API Internal CA"
$ServerCommonName  = "localhost"        # match the hostname you'll connect to
$ClientCommonName  = "api-client"
$ClientPfxPassword = "changeme"         # password for the .pfx file
$ValidityDays      = 365

Write-Host "======================================"
Write-Host " Generating mTLS certificates"
Write-Host " Output directory: $CertDir"
Write-Host "======================================"

# Check OpenSSL
try {
    openssl version | Out-Null
} catch {
    Write-Error "OpenSSL not found. Install with: winget install OpenSSL"
    exit 1
}

Push-Location $CertDir
try {
    # -----------------------------------------------------------------
    # 1. Certificate Authority (self-signed root)
    # -----------------------------------------------------------------
    Write-Host "`n[1/4] Creating Certificate Authority..."
    openssl genrsa -out ca.key 4096
    openssl req -x509 -new -nodes -key ca.key -sha256 -days $ValidityDays `
        -subj "/CN=$CACommonName/O=DMZ-API" `
        -out ca.crt

    # -----------------------------------------------------------------
    # 2. Server certificate (API service presents this)
    # -----------------------------------------------------------------
    Write-Host "`n[2/4] Creating Server certificate..."
    openssl genrsa -out server.key 2048
    openssl req -new -key server.key `
        -subj "/CN=$ServerCommonName/O=DMZ-API" `
        -out server.csr

    # Subject Alternative Names - add more hostnames/IPs as needed
    @"
subjectAltName = DNS:localhost,DNS:127.0.0.1,IP:127.0.0.1,IP:::1
extendedKeyUsage = serverAuth
"@ | Out-File -Encoding ASCII server.ext

    openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial `
        -out server.crt -days $ValidityDays -sha256 -extfile server.ext

    Remove-Item server.csr, server.ext

    # -----------------------------------------------------------------
    # 3. Client certificate (you present this when calling the API)
    # -----------------------------------------------------------------
    Write-Host "`n[3/4] Creating Client certificate..."
    openssl genrsa -out client.key 2048
    openssl req -new -key client.key `
        -subj "/CN=$ClientCommonName/O=DMZ-API" `
        -out client.csr

    @"
extendedKeyUsage = clientAuth
"@ | Out-File -Encoding ASCII client.ext

    openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial `
        -out client.crt -days $ValidityDays -sha256 -extfile client.ext

    Remove-Item client.csr, client.ext

    # -----------------------------------------------------------------
    # 4. Bundle client cert+key into a .pfx for easy import
    # -----------------------------------------------------------------
    Write-Host "`n[4/4] Creating client.pfx bundle (password: $ClientPfxPassword)..."
    openssl pkcs12 -export -out client.pfx `
        -inkey client.key -in client.crt -certfile ca.crt `
        -password "pass:$ClientPfxPassword"

    Write-Host "`n======================================"
    Write-Host " Certificates generated successfully"
    Write-Host "======================================"
    Write-Host " CA cert (trust on server):  ca.crt"
    Write-Host " Server cert+key:            server.crt / server.key"
    Write-Host " Client cert+key:            client.crt / client.key"
    Write-Host " Client bundle (Postman):    client.pfx (password: $ClientPfxPassword)"
    Write-Host "======================================"
} finally {
    Pop-Location
}
