"""Pytest configuration and fixtures for CORPORATE API tests."""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def reset_request_id():
    """Reset request ID context between tests."""
    from app.utils import request_id_var
    token = request_id_var.set("")
    yield
    request_id_var.reset(token)


@pytest.fixture
def temp_file_path(tmp_path):
    """Create a temporary file path for whitelist tests."""
    return str(tmp_path / "test_whitelist.json")


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory."""
    return tmp_path / "data"
