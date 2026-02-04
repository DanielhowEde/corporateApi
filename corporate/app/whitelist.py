"""
Project whitelist management using a JSON file.

The whitelist enforces which projects are allowed to send/receive messages
on the corporate side. Projects must be present and enabled in the file.

File format (whitelist.json):
{
  "projects": {
    "AAA": {"enabled": true},
    "BBB": {"enabled": false},
    "CCC": {"enabled": true}
  }
}
"""
import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import setup_logging

logger = setup_logging("whitelist")


class WhitelistError(Exception):
    """Exception raised for whitelist operations."""
    pass


class ProjectWhitelist:
    """
    File-based project whitelist for the Corporate API.

    Features:
    - Simple JSON file format
    - Automatic reload on file changes (checks mtime)
    - Thread-safe access
    - Runtime updates without restart

    File Format:
        {
          "projects": {
            "AAA": {"enabled": true},
            "BBB": {"enabled": false}
          }
        }
    """

    def __init__(self, file_path: Optional[str] = None):
        """
        Initialize the project whitelist.

        Args:
            file_path: Path to JSON whitelist file. If not provided, uses
                       WHITELIST_FILE_PATH env var or defaults to ./data/whitelist.json
        """
        self.file_path = Path(
            file_path or os.environ.get(
                "WHITELIST_FILE_PATH",
                "./data/whitelist.json"
            )
        )
        self._lock = threading.Lock()
        self._cache: Dict[str, dict] = {}
        self._last_mtime: float = 0
        self._init_file()

    def _init_file(self) -> None:
        """Initialize the whitelist file if it doesn't exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.file_path.exists():
            self._write_data({"projects": {}})
            logger.info(f"Created whitelist file: {self.file_path}")
        else:
            logger.info(f"Using whitelist file: {self.file_path}")

    def _read_data(self) -> Dict[str, dict]:
        """Read and parse the whitelist file."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("projects", {})
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in whitelist file: {e}")
            raise WhitelistError(f"Invalid whitelist file format: {e}")
        except OSError as e:
            logger.error(f"Failed to read whitelist file: {e}")
            raise WhitelistError(f"Failed to read whitelist file: {e}")

    def _write_data(self, data: dict) -> None:
        """Write data to the whitelist file atomically."""
        tmp_path = self.file_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.file_path)
        except OSError as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise WhitelistError(f"Failed to write whitelist file: {e}")

    def _get_projects(self) -> Dict[str, dict]:
        """
        Get projects dict, reloading from file if modified.

        Returns cached data if file hasn't changed.
        """
        with self._lock:
            try:
                current_mtime = self.file_path.stat().st_mtime
            except OSError:
                current_mtime = 0

            if current_mtime != self._last_mtime:
                self._cache = self._read_data()
                self._last_mtime = current_mtime
                logger.debug("Whitelist file reloaded")

            return self._cache

    def _save_projects(self, projects: Dict[str, dict]) -> None:
        """Save projects to file and update cache."""
        with self._lock:
            self._write_data({"projects": projects})
            self._cache = projects
            self._last_mtime = self.file_path.stat().st_mtime

    def is_project_allowed(self, project_code: str) -> bool:
        """
        Check if a project is in the whitelist and enabled.

        Args:
            project_code: The 3-character project code

        Returns:
            True if the project exists and is enabled, False otherwise
        """
        projects = self._get_projects()
        project = projects.get(project_code)

        if project is None:
            logger.debug(f"Project not in whitelist: {project_code}")
            return False

        enabled = project.get("enabled", False)
        if not enabled:
            logger.debug(f"Project disabled: {project_code}")
        return enabled

    def add_project(self, project_code: str, enabled: bool = True) -> None:
        """
        Add a project to the whitelist.

        Args:
            project_code: The 3-character project code
            enabled: Whether the project should be enabled (default True)

        Raises:
            WhitelistError: If the project already exists
        """
        projects = self._get_projects().copy()

        if project_code in projects:
            raise WhitelistError(f"Project already exists: {project_code}")

        projects[project_code] = {"enabled": enabled}
        self._save_projects(projects)
        logger.info(f"Project added: {project_code}, enabled={enabled}")

    def enable_project(self, project_code: str) -> bool:
        """
        Enable a project in the whitelist.

        Args:
            project_code: The 3-character project code

        Returns:
            True if the project was updated, False if not found
        """
        projects = self._get_projects().copy()

        if project_code not in projects:
            return False

        projects[project_code]["enabled"] = True
        self._save_projects(projects)
        logger.info(f"Project enabled: {project_code}")
        return True

    def disable_project(self, project_code: str) -> bool:
        """
        Disable a project in the whitelist.

        Args:
            project_code: The 3-character project code

        Returns:
            True if the project was updated, False if not found
        """
        projects = self._get_projects().copy()

        if project_code not in projects:
            return False

        projects[project_code]["enabled"] = False
        self._save_projects(projects)
        logger.info(f"Project disabled: {project_code}")
        return True

    def remove_project(self, project_code: str) -> bool:
        """
        Remove a project from the whitelist entirely.

        Args:
            project_code: The 3-character project code

        Returns:
            True if the project was removed, False if not found
        """
        projects = self._get_projects().copy()

        if project_code not in projects:
            return False

        del projects[project_code]
        self._save_projects(projects)
        logger.info(f"Project removed: {project_code}")
        return True

    def list_projects(self) -> List[Tuple[str, bool]]:
        """
        List all projects in the whitelist.

        Returns:
            List of tuples (project_code, enabled)
        """
        projects = self._get_projects()
        return sorted([
            (code, proj.get("enabled", False))
            for code, proj in projects.items()
        ])

    def close(self) -> None:
        """No-op for API compatibility (file-based needs no cleanup)."""
        pass
