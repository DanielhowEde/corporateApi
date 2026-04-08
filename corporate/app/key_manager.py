"""
Key generation and management for Corporate DMZ API.

Generates RSA key pairs used for message signing and encryption.
Keys are stored as PEM files in the configured keys directory.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import setup_logging

logger = setup_logging("key_manager")


class KeyManagerError(Exception):
    """Exception raised when key management operations fail."""
    pass


class KeyManager:
    """
    Manages RSA key pairs for the corporate DMZ API.

    Keys are stored in: {keys_dir}/{key_id}/
      - private.pem
      - public.pem
      - metadata.json (name, created, algorithm, size, status)
    """

    SUPPORTED_SIZES = [2048, 4096]
    DEFAULT_SIZE = 2048

    def __init__(self, keys_dir: str = None):
        from .config import config
        self.keys_dir = Path(keys_dir) if keys_dir else config.master_dir.parent / "keys"
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    def generate_key_pair(
        self, name: str, key_size: int = DEFAULT_SIZE
    ) -> Dict[str, Any]:
        """
        Generate a new RSA key pair.

        Args:
            name: Human-readable name for the key
            key_size: RSA key size in bits (2048 or 4096)

        Returns:
            Dict with key metadata

        Raises:
            KeyManagerError: If generation fails
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            raise KeyManagerError(
                "cryptography package is required for key generation. "
                "Install with: pip install cryptography"
            )

        if key_size not in self.SUPPORTED_SIZES:
            raise KeyManagerError(f"Unsupported key size: {key_size}. Use {self.SUPPORTED_SIZES}")

        key_id = str(uuid.uuid4())
        key_dir = self.keys_dir / key_id
        key_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Generate RSA key pair
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=key_size,
            )
            public_key = private_key.public_key()

            # Serialize private key (PEM, no encryption — encrypt at rest via filesystem)
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

            # Serialize public key (PEM)
            public_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )

            # Write key files
            private_path = key_dir / "private.pem"
            public_path = key_dir / "public.pem"

            with open(private_path, "wb") as f:
                f.write(private_pem)
                f.flush()
                os.fsync(f.fileno())

            with open(public_path, "wb") as f:
                f.write(public_pem)
                f.flush()
                os.fsync(f.fileno())

            # Write metadata
            metadata = {
                "key_id": key_id,
                "name": name,
                "algorithm": "RSA",
                "key_size": key_size,
                "created": datetime.now().isoformat(),
                "status": "active",
                "public_key_path": str(public_path),
                "private_key_path": str(private_path),
            }

            metadata_path = key_dir / "metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            logger.info(f"Generated RSA-{key_size} key pair: id={key_id}, name={name}")
            return metadata

        except Exception as e:
            # Clean up on failure
            import shutil
            if key_dir.exists():
                shutil.rmtree(key_dir, ignore_errors=True)
            raise KeyManagerError(f"Failed to generate key pair: {e}") from e

    def list_keys(self) -> List[Dict[str, Any]]:
        """
        List all key pairs with their metadata.

        Returns:
            List of metadata dicts, sorted by creation date (newest first)
        """
        keys = []
        if not self.keys_dir.exists():
            return keys

        for entry in self.keys_dir.iterdir():
            if entry.is_dir():
                metadata_path = entry / "metadata.json"
                if metadata_path.exists():
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            metadata = json.load(f)
                        keys.append(metadata)
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
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get_public_key_pem(self, key_id: str) -> Optional[str]:
        """Get the public key PEM string for a key."""
        public_path = self.keys_dir / key_id / "public.pem"
        if not public_path.exists():
            return None
        try:
            return public_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def revoke_key(self, key_id: str) -> bool:
        """
        Mark a key as revoked (does not delete files).

        Returns:
            True if key was found and revoked, False if not found
        """
        metadata_path = self.keys_dir / key_id / "metadata.json"
        if not metadata_path.exists():
            return False

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            metadata["status"] = "revoked"
            metadata["revoked_at"] = datetime.now().isoformat()

            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            logger.info(f"Revoked key: id={key_id}, name={metadata.get('name')}")
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to revoke key: id={key_id}, error={e}")
            return False

    def delete_key(self, key_id: str) -> bool:
        """
        Permanently delete a key pair.

        Returns:
            True if key was found and deleted, False if not found
        """
        import shutil
        key_dir = self.keys_dir / key_id
        if not key_dir.exists():
            return False

        try:
            shutil.rmtree(key_dir)
            logger.info(f"Deleted key: id={key_id}")
            return True
        except OSError as e:
            logger.error(f"Failed to delete key: id={key_id}, error={e}")
            return False
