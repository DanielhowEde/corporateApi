"""Pytest configuration and fixtures for LOW-SIDE API tests."""
import pytest


@pytest.fixture(autouse=True)
def reset_request_id():
    """Reset request ID context between tests."""
    from app.utils import request_id_var
    token = request_id_var.set("")
    yield
    request_id_var.reset(token)
