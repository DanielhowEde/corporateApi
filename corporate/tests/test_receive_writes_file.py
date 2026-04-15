"""Tests for file writing functionality on corporate side."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.file_store import FileStore, FileStoreError


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


@pytest.fixture
def temp_dirs(tmp_path):
    """Create temp directories for master, tmp, error."""
    master_dir = tmp_path / "messages"
    tmp_dir = tmp_path / "tmp"
    error_dir = tmp_path / "errors"
    return master_dir, tmp_dir, error_dir


class TestFileStore:
    """Tests for FileStore class on corporate side."""

    def test_file_store_creates_directories(self, temp_dirs):
        """Test that FileStore creates required directories."""
        master_dir, tmp_dir, error_dir = temp_dirs
        FileStore(
            master_dir=str(master_dir),
            tmp_dir=str(tmp_dir),
            error_dir=str(error_dir),
        )
        assert master_dir.exists()
        assert tmp_dir.exists()
        assert error_dir.exists()

    def test_write_message_creates_correct_path(self, temp_dirs, valid_message):
        """Test that message is written to project-based path."""
        master_dir, tmp_dir, error_dir = temp_dirs
        store = FileStore(
            master_dir=str(master_dir),
            tmp_dir=str(tmp_dir),
            error_dir=str(error_dir),
        )

        result_path = store.write_message(valid_message)

        expected_path = master_dir / "AAA" / f"{valid_message['ID']}.json"
        assert result_path == expected_path
        assert result_path.exists()

    def test_write_message_content_is_correct(self, temp_dirs, valid_message):
        """Test that written message content matches input."""
        master_dir, tmp_dir, error_dir = temp_dirs
        store = FileStore(
            master_dir=str(master_dir),
            tmp_dir=str(tmp_dir),
            error_dir=str(error_dir),
        )

        result_path = store.write_message(valid_message)

        with open(result_path) as f:
            written_data = json.load(f)

        assert written_data == valid_message


class TestReceiveEndpointWithWhitelist:
    """Integration tests for /dmz/messages with whitelist."""

    @pytest.fixture
    def configured_client(self, tmp_path):
        """Create a test client with configured whitelist and file store."""
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
        main.whitelist.add_project("AAA")

        return TestClient(main.app), master_dir

    def test_receive_writes_file_when_whitelisted(
        self, configured_client, valid_message
    ):
        """Test that receiving a message writes it to disk when whitelisted."""
        client, master_dir = configured_client

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 200

        expected_path = master_dir / "AAA" / f"{valid_message['ID']}.json"
        assert expected_path.exists()

        with open(expected_path) as f:
            written_data = json.load(f)
        assert written_data["ID"] == valid_message["ID"]

    def test_receive_returns_500_on_disk_error(
        self, configured_client, valid_message, mocker
    ):
        """Test that disk write errors return 500 with generic error."""
        client, _ = configured_client

        mocker.patch(
            "app.main.file_store.write_message",
            side_effect=FileStoreError("Disk full"),
        )

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"
        assert "disk" not in data["error"].lower()
