"""
Configuration management for Corporate DMZ API.

All configuration is loaded from environment variables with sensible defaults.
A config file (config.json) can optionally be used to set defaults.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import setup_logging

logger = setup_logging("config")


class Config:
    """
    Centralized configuration for the Corporate DMZ API.

    Configuration priority (highest to lowest):
    1. Environment variables
    2. Config file (config.json)
    3. Default values
    """

    # Default configuration values
    DEFAULTS = {
        # Branding - customize these for your organization
        "COMPANY_NAME": "Corporate",
        "SERVICE_NAME": "DMZ API",
        "NETWORK_LABEL": "CORPORATE NETWORK",

        # Master directory for message storage
        # Messages stored as: ${MASTER_DIR}/${Project}/{message_id}.json
        "MASTER_DIR": "./data/messages",

        # Temporary directory for atomic writes
        "TMP_DIR": "./data/tmp",

        # Gateway URL
        "GATEWAY_URL": "http://localhost:8000",

        # Whitelist file path
        "WHITELIST_FILE_PATH": "./data/whitelist.json",

        # Users file path
        "USERS_FILE_PATH": "./data/users.json",

        # Admin password (MUST be changed in production)
        "ADMIN_PASSWORD": "admin123",

        # Session secret (auto-generated if not set)
        "SESSION_SECRET": None,
    }

    _instance: Optional["Config"] = None
    _config: Dict[str, Any] = {}
    _config_file_path: Optional[Path] = None

    def __new__(cls):
        """Singleton pattern - only one config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self) -> None:
        """Load configuration from file and environment."""
        # Start with defaults
        self._config = self.DEFAULTS.copy()

        # Try to load config file
        config_file = Path(os.environ.get("CONFIG_FILE", "./config.json"))
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                self._config.update(file_config)
                self._config_file_path = config_file
                logger.info(f"Loaded configuration from: {config_file}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load config file {config_file}: {e}")

        # Override with environment variables
        for key in self.DEFAULTS.keys():
            env_value = os.environ.get(key)
            if env_value is not None:
                self._config[key] = env_value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def __getattr__(self, name: str) -> Any:
        """Allow attribute-style access to config values."""
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        return self._config.get(name)

    @property
    def master_dir(self) -> Path:
        """Get the master directory for message storage."""
        return Path(self._config["MASTER_DIR"])

    @property
    def tmp_dir(self) -> Path:
        """Get the temporary directory for atomic writes."""
        return Path(self._config["TMP_DIR"])

    @property
    def gateway_url(self) -> str:
        """Get the gateway URL."""
        return self._config["GATEWAY_URL"]

    @property
    def whitelist_file_path(self) -> Path:
        """Get the whitelist file path."""
        return Path(self._config["WHITELIST_FILE_PATH"])

    @property
    def users_file_path(self) -> Path:
        """Get the users file path."""
        return Path(self._config["USERS_FILE_PATH"])

    @property
    def admin_password(self) -> str:
        """Get the admin password."""
        return self._config["ADMIN_PASSWORD"]

    @property
    def company_name(self) -> str:
        """Get the company name for branding."""
        return self._config["COMPANY_NAME"]

    @property
    def service_name(self) -> str:
        """Get the service name for branding."""
        return self._config["SERVICE_NAME"]

    @property
    def network_label(self) -> str:
        """Get the network label (e.g., 'CORPORATE NETWORK')."""
        return self._config["NETWORK_LABEL"]

    @property
    def full_name(self) -> str:
        """Get the full service name (Company + Service)."""
        return f"{self._config['COMPANY_NAME']} {self._config['SERVICE_NAME']}"

    def reload(self) -> None:
        """Reload configuration from file and environment."""
        self._load_config()
        logger.info("Configuration reloaded")


# Global config instance
config = Config()
