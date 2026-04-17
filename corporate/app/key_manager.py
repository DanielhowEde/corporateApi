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

    # Default CA subject fields — used when the admin hasn't supplied
    # overrides (e.g. the very first time the CA is auto-generated).
    DEFAULT_CA_SUBJECT = {
        "common_name": "DMZ-API Internal CA",
        "organization": "DMZ-API",
        "organizational_unit": "",
        "country": "",
        "state_province": "",
        "locality": "",
        "email": "",
    }

    def _build_ca_subject(self, fields: Dict[str, str]) -> Any:
        """Assemble an x509.Name from whichever subject fields are populated."""
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        attrs = []
        if fields.get("common_name"):
            attrs.append(x509.NameAttribute(NameOID.COMMON_NAME, fields["common_name"]))
        if fields.get("organization"):
            attrs.append(
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, fields["organization"])
            )
        if fields.get("organizational_unit"):
            attrs.append(
                x509.NameAttribute(
                    NameOID.ORGANIZATIONAL_UNIT_NAME, fields["organizational_unit"]
                )
            )
        if fields.get("country"):
            attrs.append(
                x509.NameAttribute(NameOID.COUNTRY_NAME, fields["country"].upper())
            )
        if fields.get("state_province"):
            attrs.append(
                x509.NameAttribute(
                    NameOID.STATE_OR_PROVINCE_NAME, fields["state_province"]
                )
            )
        if fields.get("locality"):
            attrs.append(
                x509.NameAttribute(NameOID.LOCALITY_NAME, fields["locality"])
            )
        if fields.get("email"):
            attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, fields["email"]))

        if not attrs:
            raise KeyManagerError("CA subject must include at least a common_name")
        return x509.Name(attrs)

    def _write_ca(self, ca_key, ca_cert) -> None:
        """Write the CA key + cert to disk with fsync."""
        from cryptography.hazmat.primitives import serialization

        self.ca_dir.mkdir(parents=True, exist_ok=True)
        ca_key_path = self.ca_dir / "ca.key"
        ca_crt_path = self.ca_dir / "ca.crt"

        with open(ca_key_path, "wb") as f:
            f.write(
                ca_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            f.flush()
            os.fsync(f.fileno())

        with open(ca_crt_path, "wb") as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
            f.flush()
            os.fsync(f.fileno())

    def _generate_ca(
        self,
        subject_fields: Dict[str, str],
        validity_days: int,
        key_size: int,
    ) -> tuple:
        """Mint a brand-new CA key + self-signed cert from the given fields."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography import x509

        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        ca_subject = self._build_ca_subject(subject_fields)

        now = datetime.now(timezone.utc)
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=validity_days))
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
        return ca_key, ca_cert

    def _ensure_ca(self) -> tuple:
        """
        Load or create the CA key + cert. Returns (ca_key, ca_cert).
        Creates with defaults on first call; reuses afterwards.
        """
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography import x509
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

        logger.info("Creating new Certificate Authority (defaults)...")
        ca_key, ca_cert = self._generate_ca(
            subject_fields=self.DEFAULT_CA_SUBJECT,
            validity_days=self.CA_VALIDITY_DAYS,
            key_size=4096,
        )
        self._write_ca(ca_key, ca_cert)
        logger.info(f"CA created: {ca_crt_path}")
        return ca_key, ca_cert

    def get_ca_info(self) -> Optional[Dict[str, Any]]:
        """
        Return metadata about the current CA — subject fields, validity
        dates, SHA-256 fingerprint — suitable for display on the admin UI.
        Returns None if no CA exists yet.
        """
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
        except ImportError:
            return None

        ca_crt_path = self.ca_dir / "ca.crt"
        if not ca_crt_path.exists():
            return None

        try:
            cert = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
        except Exception as e:
            logger.warning(f"Failed to parse CA cert: {e}")
            return None

        def _first(oid) -> str:
            found = cert.subject.get_attributes_for_oid(oid)
            return found[0].value if found else ""

        fingerprint = cert.fingerprint(hashes.SHA256()).hex(":").upper()
        return {
            "common_name": _first(NameOID.COMMON_NAME),
            "organization": _first(NameOID.ORGANIZATION_NAME),
            "organizational_unit": _first(NameOID.ORGANIZATIONAL_UNIT_NAME),
            "country": _first(NameOID.COUNTRY_NAME),
            "state_province": _first(NameOID.STATE_OR_PROVINCE_NAME),
            "locality": _first(NameOID.LOCALITY_NAME),
            "email": _first(NameOID.EMAIL_ADDRESS),
            "not_valid_before": cert.not_valid_before_utc.isoformat(),
            "not_valid_after": cert.not_valid_after_utc.isoformat(),
            "serial_number": str(cert.serial_number),
            "fingerprint_sha256": fingerprint,
        }

    def mark_all_keys_orphaned(self) -> int:
        """
        Mark every non-CA key's metadata with status='orphaned' after a CA
        regeneration. Existing client certs were signed by the retired CA
        and cannot be validated by the new one.

        Returns the number of key pairs marked.
        """
        marked = 0
        if not self.keys_dir.exists():
            return 0
        for entry in self.keys_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            meta_path = entry / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                if metadata.get("status") != "orphaned":
                    metadata["status"] = "orphaned"
                    metadata["orphaned_at"] = datetime.now().isoformat()
                    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                    marked += 1
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to mark key orphaned: {entry.name}, error={e}")
        return marked

    def regenerate_ca(
        self,
        subject_fields: Dict[str, str],
        validity_days: int = CA_VALIDITY_DAYS,
        key_size: int = 4096,
    ) -> Dict[str, Any]:
        """
        Archive the existing CA, mint a new one from the supplied subject,
        and mark all existing client certs as orphaned.

        Args:
            subject_fields: dict with any of: common_name (required),
                organization, organizational_unit, country, state_province,
                locality, email.
            validity_days: CA validity window (default 3650 = 10 years).
            key_size: RSA key size (2048 or 4096).

        Returns:
            Dict summarising the operation:
              - archived_path: str (or None if no previous CA)
              - orphaned_keys: int
              - ca_info: dict (same shape as get_ca_info)
        """
        try:
            import cryptography  # noqa: F401
        except ImportError:
            raise KeyManagerError(
                "cryptography package is required. Install: pip install cryptography"
            )

        if key_size not in self.SUPPORTED_SIZES:
            raise KeyManagerError(
                f"Unsupported key size: {key_size}. Use {self.SUPPORTED_SIZES}"
            )
        if validity_days <= 0 or validity_days > 3650 * 5:
            raise KeyManagerError("validity_days must be between 1 and 18250")

        # 1. Archive existing CA directory (if any)
        archived_path = None
        if self.ca_dir.exists() and any(self.ca_dir.iterdir()):
            import shutil

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_dir = self.keys_dir / f"_ca.archived.{stamp}"
            shutil.move(str(self.ca_dir), str(archive_dir))
            archived_path = str(archive_dir)
            logger.info(f"Archived old CA to {archive_dir}")

        # 2. Mint new CA
        ca_key, ca_cert = self._generate_ca(subject_fields, validity_days, key_size)
        self._write_ca(ca_key, ca_cert)
        logger.info(
            f"Regenerated CA: subject={subject_fields}, "
            f"validity_days={validity_days}, key_size={key_size}"
        )

        # 3. Flag existing client certs as orphaned
        orphaned = self.mark_all_keys_orphaned()
        logger.info(f"Marked {orphaned} existing client key(s) as orphaned")

        return {
            "archived_path": archived_path,
            "orphaned_keys": orphaned,
            "ca_info": self.get_ca_info(),
        }

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
