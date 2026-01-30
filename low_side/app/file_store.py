"""
File storage with atomic writes for DMZ messages.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict

from .utils import parse_date_components


class FileStoreError(Exception):
    """Exception raised when file storage operations fail."""
    pass


class FileStore:
    """
    Handles atomic file writes for incoming messages.

    File structure: {data_dir}/incoming/YYYY/MM/DD/{message_id}.json

    Atomic write process:
    1. Write to {data_dir}/tmp/{message_id}.json.tmp
    2. fsync to ensure data is on disk
    3. Rename to final destination (atomic on POSIX)
    """

    def __init__(self, data_dir: str = "./data"):
        """
        Initialize file store.

        Args:
            data_dir: Base directory for file storage
        """
        self.data_dir = Path(data_dir)
        self.tmp_dir = self.data_dir / "tmp"
        self.incoming_dir = self.data_dir / "incoming"

        # Ensure base directories exist
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create base directories if they don't exist."""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir.mkdir(parents=True, exist_ok=True)

    def _get_final_path(self, message_id: str, date_str: str) -> Path:
        """
        Get the final file path for a message.

        Args:
            message_id: UUID of the message
            date_str: Date string in ddMMyyyyThh:mm:ss format

        Returns:
            Path object for the final file location
        """
        year, month, day = parse_date_components(date_str)
        return self.incoming_dir / year / month / day / f"{message_id}.json"

    def _get_tmp_path(self, message_id: str) -> Path:
        """
        Get the temporary file path for a message.

        Args:
            message_id: UUID of the message

        Returns:
            Path object for the temporary file
        """
        return self.tmp_dir / f"{message_id}.json.tmp"

    def write_message(self, message_data: Dict[str, Any]) -> Path:
        """
        Write a message to disk atomically.

        Args:
            message_data: Dictionary containing the message data

        Returns:
            Path to the written file

        Raises:
            FileStoreError: If the write operation fails
        """
        message_id = message_data["ID"]
        date_str = message_data["Date"]

        tmp_path = self._get_tmp_path(message_id)
        final_path = self._get_final_path(message_id, date_str)

        try:
            # Ensure the final directory exists
            final_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: Write to temporary file
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(message_data, f, indent=2, ensure_ascii=False)
                # Step 2: fsync to ensure data is written to disk
                f.flush()
                os.fsync(f.fileno())

            # Step 3: Atomic rename to final destination
            tmp_path.rename(final_path)

            return final_path

        except OSError as e:
            # Clean up temporary file if it exists
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise FileStoreError(f"Failed to write message file: {e}") from e
        except Exception as e:
            # Clean up temporary file if it exists
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise FileStoreError(f"Unexpected error writing message file: {e}") from e
