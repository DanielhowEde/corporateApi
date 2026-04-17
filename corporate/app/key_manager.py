"""
Key generation and certificate issuance for Corporate DMZ API.

Generates RSA key pairs and issues X.509 client certificates signed by a
self-managed Certificate Authority. The app acts as its own CA: it creates
the CA on first use, then signs client certs that can be used for mTLS.

Files in {keys_dir}:
    _ca/
      ca.key           - CA private key (keep secret)
      ca.crt           - CA public cert (distribute to servers for trust)
    {key_id}/
      private.pem      - Client private key
      public.pem       - Client public key
      client.crt       - Client certificate signed by the CA
      client.pfx       - PKCS#12 bundle (cert + key) for Postman/browsers
      metadata.json    - Metadata (name, created, status, etc.)
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import setup_logging

logger = setup_logging("key_manager")


class KeyManagerError(Exception):
    """Exception raised when key management operations fail."""

    pass


class KeyManager:
    """
    Manages RSA key pairs and X.509 client certificates.

    Acts as its own Certificate Authority — maintains a CA key/cert pair
    and issues client certificates signed by that CA. The CA cert can be
    exported and trusted by any server that wants to validate incoming
    mTLS clients holding certs this manager issued.
    """

    SUPPORTED_SIZES = [2048, 4096]
    DEFAULT_SIZE = 2048
    CERT_VALIDITY_DAYS = 365
    CA_VALIDITY_DAYS = 3650  # 10 years
    DEFAULT_PFX_PASSWORD = "changeme"

    def __init__(self, keys_dir: str = None):
        from .config import config

        self.keys_dir = (
            Path(keys_dir) if keys_dir else config.master_dir.parent / "keys"
        )
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.ca_dir = self.keys_dir / "_ca"

    # -------------------------------------------------------------------
    # Certificate Authority
    # -------------------------------------------------------------------

    def _ensure_ca(self) -> tuple:
        """
        Load or create the CA key + cert. Returns (ca_key, ca_cert).
        Creates them on first call; reuses them afterwards.
        """
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography import x509
            from cryptography.x509.oid import NameOID
        except ImportError:
            raise KeyManagerError(
                "cryptography package is required. Install: pip install cryptography"
            )

        self.ca_dir.mkdir(parents=True, exist_ok=True)
        ca_key_path = self.ca_dir / "ca.key"
        ca_crt_path = self.ca_dir / "ca.crt"

        if ca_key_path.exists() and ca_crt_path.exists():
            ca_key = serialization.load_pem_private_key(
                ca_key_path.read_bytes(), password=None
            )
            ca_cert = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
            return ca_key, ca_cert

        logger.info("Creating new Certificate Authority...")
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

        ca_subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "DMZ-API Internal CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DMZ-API"),
            ]
        )

        now = datetime.now(timezone.utc)
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=self.CA_VALIDITY_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        ca_crt_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"CA created: {ca_crt_path}")
        return ca_key, ca_cert

    def get_ca_cert_pem(self) -> Optional[str]:
        """Return the CA cert as PEM string, creating it if needed."""
        self._ensure_ca()
        ca_crt = self.ca_dir / "ca.crt"
        return ca_crt.read_text(encoding="utf-8") if ca_crt.exists() else None

    # -------------------------------------------------------------------
    # Key pair + client cert generation
    # -------------------------------------------------------------------

    def generate_key_pair(
        self, name: str, key_size: int = DEFAULT_SIZE
    ) -> Dict[str, Any]:
        """
        Generate an RSA key pair and issue a client certificate signed by
        the local CA. The certificate can be used for mTLS authentication.

        Args:
            name: Human-readable name (used as CN in the cert subject)
            key_size: RSA key size in bits (2048 or 4096)

        Returns:
            Metadata dict with key_id, paths, and cert details

        Raises:
            KeyManagerError: If generation fails
        """
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives.serialization import pkcs12
            from cryptography import x509
            from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        except ImportError:
            raise KeyManagerError(
                "cryptography package is required. Install: pip install cryptography"
            )

        if key_size not in self.SUPPORTED_SIZES:
            raise KeyManagerError(
                f"Unsupported key size: {key_size}. Use {self.SUPPORTED_SIZES}"
            )

        ca_key, ca_cert = self._ensure_ca()

        key_id = str(uuid.uuid4())
        key_dir = self.keys_dir / key_id
        key_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Generate RSA key pair
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=key_size
            )
            public_key = private_key.public_key()

            # Build client cert signed by the CA
            subject = x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DMZ-API"),
                ]
            )
            now = datetime.now(timezone.utc)
            client_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(public_key)
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + timedelta(days=self.CERT_VALIDITY_DAYS))
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None), critical=True
                )
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                    critical=False,
                )
                .sign(ca_key, hashes.SHA256())
            )

            def _write_with_sync(path: Path, data: bytes) -> None:
                """Write bytes and fsync — fsync requires a writable fd on Windows."""
                with open(path, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())

            # Private key
            private_path = key_dir / "private.pem"
            _write_with_sync(
                private_path,
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
            )

            # Public key
            public_path = key_dir / "public.pem"
            _write_with_sync(
                public_path,
                public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
            )

            # Client cert
            cert_path = key_dir / "client.crt"
            _write_with_sync(cert_path, client_cert.public_bytes(serialization.Encoding.PEM))

            # PFX bundle (cert + key + CA chain)
            pfx_path = key_dir / "client.pfx"
            pfx_bytes = pkcs12.serialize_key_and_certificates(
                name=name.encode("utf-8"),
                key=private_key,
                cert=client_cert,
                cas=[ca_cert],
                encryption_algorithm=serialization.BestAvailableEncryption(
                    self.DEFAULT_PFX_PASSWORD.encode("utf-8")
                ),
            )
            _write_with_sync(pfx_path, pfx_bytes)

            # Metadata
            metadata = {
                "key_id": key_id,
                "name": name,
                "algorithm": "RSA",
                "key_size": key_size,
                "created": datetime.now().isoformat(),
                "status": "active",
                "cert_subject": f"CN={name},O=DMZ-API",
                "cert_issuer": "CN=DMZ-API Internal CA,O=DMZ-API",
                "cert_not_before": now.isoformat(),
                "cert_not_after": (
                    now + timedelta(days=self.CERT_VALIDITY_DAYS)
                ).isoformat(),
                "serial_number": str(client_cert.serial_number),
                "pfx_password": self.DEFAULT_PFX_PASSWORD,
            }
            (key_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )

            logger.info(
                f"Generated key pair + client cert: id={key_id}, name={name}, "
                f"size={key_size}"
            )
            return metadata

        except Exception as e:
            import shutil

            if key_dir.exists():
                shutil.rmtree(key_dir, ignore_errors=True)
            raise KeyManagerError(f"Failed to generate key pair: {e}") from e

    # -------------------------------------------------------------------
    # Listing, retrieval, revocation
    # -------------------------------------------------------------------

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all key pairs with metadata, newest first. Skips the CA."""
        keys = []
        if not self.keys_dir.exists():
            return keys

        for entry in self.keys_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            metadata_path = entry / "metadata.json"
            if not metadata_path.exists():
                continue
            try:
                keys.append(json.loads(metadata_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read key metadata: {entry.name}, error={e}")

        keys.sort(key=lambda k: k.get("created", ""), reverse=True)
        return keys

    def get_key(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific key."""
        metadata_path = self.keys_dir / key_id / "metadata.json"
        if not metadata_path.exists():
            return None
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get_public_key_pem(self, key_id: str) -> Optional[str]:
        """Return the public key PEM string."""
        p = self.keys_dir / key_id / "public.pem"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def get_file(self, key_id: str, filename: str) -> Optional[Path]:
        """
        Return a Path to a file inside a key directory, if it exists and is
        one of the allowed export filenames.
        """
        allowed = {"private.pem", "public.pem", "client.crt", "client.pfx"}
        if filename not in allowed:
            return None
        p = self.keys_dir / key_id / filename
        return p if p.exists() else None

    def revoke_key(self, key_id: str) -> bool:
        """Mark a key as revoked (files preserved for audit)."""
        metadata_path = self.keys_dir / key_id / "metadata.json"
        if not metadata_path.exists():
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["status"] = "revoked"
            metadata["revoked_at"] = datetime.now().isoformat()
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            logger.info(f"Revoked key: id={key_id}, name={metadata.get('name')}")
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to revoke key: id={key_id}, error={e}")
            return False

    def delete_key(self, key_id: str) -> bool:
        """Permanently delete a key pair."""
        import shutil

        key_dir = self.keys_dir / key_id
        if not key_dir.exists() or key_id.startswith("_"):
            return False
        try:
            shutil.rmtree(key_dir)
            logger.info(f"Deleted key: id={key_id}")
            return True
        except OSError as e:
            logger.error(f"Failed to delete key: id={key_id}, error={e}")
            return False
