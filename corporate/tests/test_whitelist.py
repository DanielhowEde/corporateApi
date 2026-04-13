"""Tests for project whitelist functionality."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.whitelist import ProjectWhitelist, WhitelistError


@pytest.fixture
def whitelist(temp_file_path):
    """Create a whitelist instance with temporary file."""
    wl = ProjectWhitelist(file_path=temp_file_path)
    yield wl
    wl.close()


@pytest.fixture
def valid_message():
    """Create a valid message payload."""
    return {
        "ID": str(uuid.uuid4()),
        "Project": "AAA",
        "TestID": "AAA-1112",
        "Area": "Test Area",
        "Status": "Inprogress",
        "Date": "2026-01-30T11:22:33",
        "Data": {"random": "A", "name": "john smith"},
    }


class TestProjectWhitelist:
    """Tests for ProjectWhitelist class."""

    def test_add_project(self, whitelist):
        """Test adding a project to the whitelist."""
        whitelist.add_project("AAA")
        assert whitelist.is_project_allowed("AAA") is True

    def test_add_project_disabled(self, whitelist):
        """Test adding a disabled project."""
        whitelist.add_project("BBB", enabled=False)
        assert whitelist.is_project_allowed("BBB") is False

    def test_add_duplicate_project_raises_error(self, whitelist):
        """Test that adding a duplicate project raises an error."""
        whitelist.add_project("AAA")
        with pytest.raises(WhitelistError):
            whitelist.add_project("AAA")

    def test_enable_project(self, whitelist):
        """Test enabling a disabled project."""
        whitelist.add_project("CCC", enabled=False)
        assert whitelist.is_project_allowed("CCC") is False

        whitelist.enable_project("CCC")
        assert whitelist.is_project_allowed("CCC") is True

    def test_disable_project(self, whitelist):
        """Test disabling an enabled project."""
        whitelist.add_project("DDD", enabled=True)
        assert whitelist.is_project_allowed("DDD") is True

        whitelist.disable_project("DDD")
        assert whitelist.is_project_allowed("DDD") is False

    def test_enable_nonexistent_project_returns_false(self, whitelist):
        """Test that enabling a non-existent project returns False."""
        assert whitelist.enable_project("XXX") is False

    def test_disable_nonexistent_project_returns_false(self, whitelist):
        """Test that disabling a non-existent project returns False."""
        assert whitelist.disable_project("YYY") is False

    def test_remove_project(self, whitelist):
        """Test removing a project from the whitelist."""
        whitelist.add_project("EEE")
        assert whitelist.is_project_allowed("EEE") is True

        assert whitelist.remove_project("EEE") is True
        assert whitelist.is_project_allowed("EEE") is False

    def test_remove_nonexistent_project_returns_false(self, whitelist):
        """Test that removing a non-existent project returns False."""
        assert whitelist.remove_project("ZZZ") is False

    def test_list_projects(self, whitelist):
        """Test listing all projects."""
        whitelist.add_project("AAA", enabled=True)
        whitelist.add_project("BBB", enabled=False)
        whitelist.add_project("CCC", enabled=True)

        projects = whitelist.list_projects()

        assert len(projects) == 3
        assert ("AAA", True) in projects
        assert ("BBB", False) in projects
        assert ("CCC", True) in projects

    def test_list_projects_empty(self, whitelist):
        """Test listing projects when empty."""
        assert whitelist.list_projects() == []

    def test_is_project_allowed_not_in_whitelist(self, whitelist):
        """Test that a project not in whitelist is not allowed."""
        assert whitelist.is_project_allowed("XXX") is False

    def test_file_is_created(self, temp_file_path):
        """Test that the whitelist file is created automatically."""
        import os

        wl = ProjectWhitelist(file_path=temp_file_path)
        assert os.path.exists(temp_file_path)
        wl.close()

    def test_file_format_is_correct(self, temp_file_path):
        """Test that the file format is valid JSON."""
        wl = ProjectWhitelist(file_path=temp_file_path)
        wl.add_project("AAA", enabled=True)
        wl.add_project("BBB", enabled=False)

        with open(temp_file_path) as f:
            data = json.load(f)

        assert "projects" in data
        assert data["projects"]["AAA"]["enabled"] is True
        assert data["projects"]["BBB"]["enabled"] is False
        wl.close()

    def test_manual_file_edit_detected(self, temp_file_path):
        """Test that manual file edits are detected on next check."""
        import time

        wl = ProjectWhitelist(file_path=temp_file_path)
        wl.add_project("AAA", enabled=True)
        assert wl.is_project_allowed("AAA") is True

        time.sleep(0.1)
        with open(temp_file_path, "w") as f:
            json.dump({"projects": {"AAA": {"enabled": False}}}, f)

        assert wl.is_project_allowed("AAA") is False
        wl.close()


class TestWhitelistIntegration:
    """Integration tests for whitelist with API endpoints."""

    @pytest.fixture
    def configured_client(self, tmp_path):
        """Create a test client with configured whitelist."""
        master_dir = tmp_path / "messages"
        tmp_dir = tmp_path / "tmp"
        error_dir = tmp_path / "errors"
        whitelist_path = tmp_path / "whitelist.json"

        from app import main
        from app.whitelist import ProjectWhitelist
        from app.file_store import FileStore

        main.whitelist = ProjectWhitelist(file_path=str(whitelist_path))
        main.file_store = FileStore(
            master_dir=str(master_dir),
            tmp_dir=str(tmp_dir),
            error_dir=str(error_dir),
        )

        return TestClient(main.app), main.whitelist

    def test_send_message_project_not_whitelisted(
        self, configured_client, valid_message
    ):
        """Test that sending a message with non-whitelisted project returns 400."""
        client, _ = configured_client

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"

    def test_send_message_project_whitelisted(
        self, configured_client, valid_message, mocker
    ):
        """Test that sending a message with whitelisted project succeeds."""
        client, whitelist = configured_client
        whitelist.add_project("AAA")

        from app import main
        from app.gateway_client import GatewayClient

        if not hasattr(main, "gateway_client"):
            main.gateway_client = GatewayClient()

        mocker.patch.object(
            main.gateway_client, "send_message", return_value={"success": True}
        )

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_send_message_project_disabled(self, configured_client, valid_message):
        """Test that sending a message with disabled project returns 400."""
        client, whitelist = configured_client
        whitelist.add_project("AAA", enabled=False)

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_receive_message_project_not_whitelisted(
        self, configured_client, valid_message
    ):
        """Test that receiving a message with non-whitelisted project returns 400."""
        client, _ = configured_client

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_receive_message_project_whitelisted(
        self, configured_client, valid_message
    ):
        """Test that receiving a message with whitelisted project succeeds."""
        client, whitelist = configured_client
        whitelist.add_project("AAA")

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message_id"] == valid_message["ID"]

    def test_whitelist_update_without_restart(self, configured_client, valid_message):
        """Test that whitelist updates take effect without restart."""
        client, whitelist = configured_client

        response = client.post("/dmz/messages", json=valid_message)
        assert response.status_code == 400

        whitelist.add_project("AAA")

        response = client.post("/dmz/messages", json=valid_message)
        assert response.status_code == 200

        whitelist.disable_project("AAA")

        response = client.post("/dmz/messages", json=valid_message)
        assert response.status_code == 400

    def test_error_does_not_reveal_whitelist_details(
        self, configured_client, valid_message
    ):
        """Test that error responses don't reveal whitelist details."""
        client, _ = configured_client

        response = client.post("/messages", json=valid_message)

        data = response.json()
        assert "whitelist" not in data["error"].lower()
        assert data["error"] == "Invalid request"
