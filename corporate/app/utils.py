"""
Utility functions for DMZ API.
"""
import logging
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Callable

# Context variable for request ID tracking
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def generate_request_id() -> str:
    """Generate a new UUID request ID."""
    return str(uuid.uuid4())


def get_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    request_id_var.set(request_id)


class RequestIdFilter(logging.Filter):
    """Logging filter to add request_id to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def setup_logging(service_name: str) -> logging.Logger:
    """
    Configure logging with request ID tracking.

    Args:
        service_name: Name of the service for the logger

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - "
            "[request_id=%(request_id)s] - %(message)s"
        )
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())

        logger.addHandler(handler)

    return logger


def parse_date_components(date_str: str) -> tuple[str, str, str]:
    """
    Parse date components from ddMMyyyyThh:mm:ss format.

    Args:
        date_str: Date string in ddMMyyyyThh:mm:ss format

    Returns:
        Tuple of (year, month, day)
    """
    # Format: ddMMyyyyThh:mm:ss
    day = date_str[0:2]
    month = date_str[2:4]
    year = date_str[4:8]
    return year, month, day
