"""
Certificate store for low-side.

Stores the corporate CA cert and issued client certs synced from corporate
via the gateway. The CA cert is used as the trust root for mTLS — client
certs issued by corporate can be validated by low-side without the private
key ever leaving corporate.

File layout in {keys_dir}:
    ca.crt                      - Corporate CA certificate
    clients/
      {key_id}.json             - Metadata + cert PEM for each synced client
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import setup_logging

logger = setup_logging("cert_store")


class CertStoreError(Exception):
    """Exception raised when cert store operations fail."""

    pass


class CertStore:
    """Read/write store for CA cert and synced client certs."""

    def __init__(self, keys_dir: Optional[str] = None):
        from .config import config

        self.keys_dir = (
            Path(keys_dir) if keys_dir else config.master_dir.parent / "keys"
        )
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.clients_dir = self.keys_dir / "clients"
        self.clients_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ca_path(self) -> Path:
        return self.keys_dir / "ca.crt"

    def save_ca(self, ca_pem: str) -> Path:
        """Save the CA cert. Overwrites if it already exists."""
        try:
            with open(self.ca_path, "w", encoding="utf-8") as f:
                f.write(ca_pem)
                f.flush()
                os.fsync(f.fileno())
            logger.info(f"CA cert saved: {self.ca_path}")
            return self.ca_path
        except OSError as e:
            raise CertStoreError(f"Failed to save CA cert: {e}") from e

    def get_ca_pem(self) -> Optional[str]:
        """Return the CA cert as PEM, or None if not synced yet."""
        if not self.ca_path.exists():
            return None
        try:
            return self.ca_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def save_client_cert(self, cert_data: Dict[str, Any]) -> Path:
        """
        Save a synced client cert record.

        Args:
            cert_data: Dict with at minimum:
                - key_id: Client key identifier
                - name: Human-readable name
                - cert_pem: Client certificate in PEM format
                - action: upsert | revoke | delete
        """
        key_id = cert_data.get("key_id")
        if not key_id:
            raise CertStoreError("key_id required")

        action = cert_data.get("action", "upsert")
        cert_path = self.clients_dir / f"{key_id}.json"

        if action == "delete":
            if cert_path.exists():
                cert_path.unlink()
                logger.info(f"Client cert deleted: key_id={key_id}")
            return cert_path

        # upsert / revoke — store metadata + PEM
        record = {
            "key_id": key_id,
            "name": cert_data.get("name", ""),
            "cert_pem": cert_data.get("cert_pem", ""),
            "status": "revoked" if action == "revoke" else "active",
            "synced_at": datetime.now().isoformat(),
        }
        try:
            with open(cert_path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            logger.info(f"Client cert synced: key_id={key_id}, status={record['status']}")
            return cert_path
        except OSError as e:
            raise CertStoreError(f"Failed to save client cert: {e}") from e

    def list_clients(self) -> List[Dict[str, Any]]:
        """List all synced client cert records, newest first."""
        records = []
        if not self.clients_dir.exists():
            return records
        for f in self.clients_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    records.append(json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        records.sort(key=lambda r: r.get("synced_at", ""), reverse=True)
        return records
