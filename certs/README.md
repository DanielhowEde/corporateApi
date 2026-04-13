# mTLS Certificates

Generate certificates to enable **mutual TLS** — the server presents a cert to prove its identity, and the client must also present a cert signed by a trusted CA.

## 1. Generate Certificates

**Windows (PowerShell):**
```powershell
cd certs
.\generate_certs.ps1
```

**Linux/Mac:**
```bash
cd certs
chmod +x generate_certs.sh
./generate_certs.sh
```

Requires OpenSSL on PATH. Install:
- Windows: `winget install OpenSSL` or `choco install openssl`
- Mac: `brew install openssl`
- Linux: `apt install openssl` (usually pre-installed)

Files produced:

| File | Purpose |
|------|---------|
| `ca.crt` | CA public cert — server uses this to verify clients |
| `ca.key` | CA private key — keep secret, needed to sign new certs |
| `server.crt` + `server.key` | Server cert + key — uvicorn presents these |
| `client.crt` + `client.key` | Client cert + key — you present these when calling API |
| `client.pfx` | PKCS#12 bundle for Postman/browsers (password: `changeme`) |

## 2. Start Service with mTLS

Run uvicorn with TLS + client cert verification:

```bash
cd corporate
python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile ../certs/server.key \
  --ssl-certfile ../certs/server.crt \
  --ssl-ca-certs ../certs/ca.crt \
  --ssl-cert-reqs 2
```

`--ssl-cert-reqs 2` = `CERT_REQUIRED` (client MUST present a valid cert). Value `1` = optional, `0` = none.

The service is now reachable at `https://localhost:8443` **only** with a valid client cert.

## 3. Call the API with Your Client Cert

### curl
```bash
curl --cert certs/client.crt --key certs/client.key --cacert certs/ca.crt \
     https://localhost:8443/health
```

### PowerShell
```powershell
# PowerShell 7+ uses -Certificate parameter
$cert = Get-PfxCertificate -FilePath certs/client.pfx
Invoke-RestMethod -Uri https://localhost:8443/health -Certificate $cert
```

### Python (httpx)
```python
import httpx

client = httpx.Client(
    cert=("certs/client.crt", "certs/client.key"),
    verify="certs/ca.crt",
)
r = client.get("https://localhost:8443/health")
print(r.status_code, r.json())
```

### Postman
1. Settings → Certificates → Add Certificate
2. Host: `localhost:8443`
3. CRT file: `certs/client.crt`
4. KEY file: `certs/client.key`
5. Enable "CA certificates" and load `ca.crt`
6. Send requests normally — Postman attaches the cert

### Browser
Import `client.pfx` (password `changeme`) into your OS/browser cert store. The browser will prompt you to pick a cert when connecting to `https://localhost:8443`.

## 4. Without Client Cert = Rejected

Calls without a valid cert will fail with a TLS handshake error:
```
curl: (35) error:0A000410:SSL routines::sslv3 alert handshake failure
```

That's the mTLS enforcement working — exactly what you want.

## Security Notes

- **Never commit `*.key` or `*.pfx` files** — `.gitignore` in this directory blocks them
- **Change `PFX_PASSWORD`** in the generator scripts before using in production
- **Rotate certs** — default validity is 365 days, re-run the script to regenerate
- **Protect `ca.key`** — anyone with it can mint valid client certs. Store offline in production.
- For production, use a real CA (Let's Encrypt for server, your corporate CA for clients), not this self-signed setup.

## Issuing Additional Client Certs

To add a new client without regenerating everything, use the existing CA:

```bash
cd certs
openssl genrsa -out client2.key 2048
openssl req -new -key client2.key -subj "/CN=another-client/O=DMZ-API" -out client2.csr
echo "extendedKeyUsage = clientAuth" > client2.ext
openssl x509 -req -in client2.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out client2.crt -days 365 -sha256 -extfile client2.ext
rm client2.csr client2.ext
```

Each client gets its own cert signed by the same CA.
