"""
File storage with atomic writes for DMZ messages.

File structure: ${MASTER_DIR}/${Project}/{message_id}.json

This keeps messages organized by project for easy management and retrieval.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List


class FileStoreError(Exception):
    """Exception raised when file storage operations fail."""
    pass


class FileStore:
    """
    Handles atomic file writes for incoming messages.

    File structure: {master_dir}/{Project}/{message_id}.json

    This organization:
    - Keeps project files separate for easier management
    - Simple flat structure within each project using UUID as filename
    - Easy to find messages by ID

    Atomic write process:
    1. Write to {tmp_dir}/{message_id}.json.tmp
    2. fsync to ensure data is on disk
    3. Rename to final destination (atomic on POSIX)
    """

    def __init__(self, master_dir: str = None, tmp_dir: str = None, error_dir: str = None):
        """
        Initialize file store.

        Args:
            master_dir: Master directory for message storage.
                        Messages stored as: {master_dir}/{Project}/{id}.json
                        Defaults to MASTER_DIR from config or ./data/messages
            tmp_dir: Temporary directory for atomic writes.
                     Defaults to TMP_DIR from config or ./data/tmp
            error_dir: Directory for storing error records.
                       Defaults to ERROR_DIR from config or ./data/errors
        """
        # Import config here to avoid circular imports
        from .config import config

        # Use provided values or fall back to config
        self.master_dir = Path(master_dir) if master_dir else config.master_dir
        self.tmp_dir = Path(tmp_dir) if tmp_dir else config.tmp_dir
        self.error_dir = Path(error_dir) if error_dir else config.error_dir

        # Ensure base directories exist
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create base directories if they don't exist."""
        self.master_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.error_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_project_dir(self, project: str) -> Path:
        """
        Ensure project directory exists and return its path.

        Args:
            project: Project code (3-character code)

        Returns:
            Path to the project directory
        """
        project_dir = self.master_dir / project
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    def _get_final_path(self, message_id: str, project: str) -> Path:
        """
        Get the final file path for a message.

        Args:
            message_id: UUID of the message
            project: Project code (3-character code)

        Returns:
            Path object for the final file location:
            {master_dir}/{Project}/{message_id}.json
        """
        return self.master_dir / project / f"{message_id}.json"

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

        Messages are stored in project-specific directories:
        {master_dir}/{Project}/{message_id}.json

        Args:
            message_data: Dictionary containing the message data
                          Must include: ID, Project

        Returns:
            Path to the written file

        Raises:
            FileStoreError: If the write operation fails
        """
        message_id = message_data["ID"]
        project = message_data["Project"]

        tmp_path = self._get_tmp_path(message_id)
        final_path = self._get_final_path(message_id, project)

        try:
            # Ensure the project directory exists
            final_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: Write to temporary file
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(message_data, f, indent=2, ensure_ascii=False)
                # Step 2: fsync to ensure data is written to disk
                f.flush()
                os.fsync(f.fileno())

            # Step 3: Atomic rename to final destination
            os.replace(tmp_path, final_path)

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

    def write_error(self, error_data: Dict[str, Any]) -> Path:
        """
        Write an error record to the error directory.

        Error files are stored as: {error_dir}/{error_id}.json

        Args:
            error_data: Dictionary containing error details.

        Returns:
            Path to the written error file

        Raises:
            FileStoreError: If the write operation fails
        """
        import uuid as _uuid
        from datetime import datetime

        error_id = error_data.get("error_id", str(_uuid.uuid4()))
        if "timestamp" not in error_data:
            error_data["timestamp"] = datetime.now().isoformat()
        error_data["error_id"] = error_id

        error_path = self.error_dir / f"{error_id}.json"

        try:
            with open(error_path, "w", encoding="utf-8") as f:
                json.dump(error_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            return error_path
        except OSError as e:
            raise FileStoreError(f"Failed to write error file: {e}") from e

    def get_project_dir(self, project: str) -> Path:
        """
        Get the directory path for a specific project.
        Creates the directory if it doesn't exist.

        Args:
            project: Project code (3-character code)

        Returns:
            Path to the project directory
        """
        return self._ensure_project_dir(project)

    def list_projects(self) -> List[str]:
        """
        List all projects that have message directories.

        Returns:
            List of project codes that have directories
        """
        if not self.master_dir.exists():
            return []
        return sorted([
            d.name for d in self.master_dir.iterdir()
            if d.is_dir() and len(d.name) == 3  # Project codes are 3 chars
        ])

    def list_messages(self, project: str) -> List[str]:
        """
        List all message IDs in a project directory.

        Args:
            project: Project code (3-character code)

        Returns:
            List of message IDs (UUIDs)
        """
        project_dir = self.master_dir / project
        if not project_dir.exists():
            return []
        return sorted([
            f.stem for f in project_dir.iterdir()
            if f.is_file() and f.suffix == ".json"
        ])
