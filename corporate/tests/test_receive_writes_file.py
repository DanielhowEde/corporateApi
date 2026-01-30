"""Tests for file writing functionality on corporate side."""
import json
import uuid
from pathlib import Path

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
        "Date": "30012026T11:22:33",
        "Data": {
            "random": "A",
            "name": "john smith"
        }
    }


class TestFileStore:
    """Tests for FileStore class on corporate side."""

    def test_file_store_creates_directories(self, temp_data_dir):
        """Test that FileStore creates required directories."""
        store = FileStore(data_dir=str(temp_data_dir))

        assert (temp_data_dir / "tmp").exists()
        assert (temp_data_dir / "incoming").exists()

    def test_write_message_creates_correct_path(self, temp_data_dir, valid_message):
        """Test that message is written to correct date-based path."""
        store = FileStore(data_dir=str(temp_data_dir))

        result_path = store.write_message(valid_message)

        # Date is 30012026 -> 2026/01/30
        expected_path = (
            temp_data_dir / "incoming" / "2026" / "01" / "30" /
            f"{valid_message['ID']}.json"
        )
        assert result_path == expected_path
        assert result_path.exists()

    def test_write_message_content_is_correct(self, temp_data_dir, valid_message):
        """Test that written message content matches input."""
        store = FileStore(data_dir=str(temp_data_dir))

        result_path = store.write_message(valid_message)

        with open(result_path) as f:
            written_data = json.load(f)

        assert written_data == valid_message


class TestReceiveEndpointWithWhitelist:
    """Integration tests for /dmz/messages with whitelist."""

    @pytest.fixture
    def configured_client(self, tmp_path, monkeypatch):
        """Create a test client with configured whitelist and file store."""
        data_dir = tmp_path / "data"
        db_path = tmp_path / "whitelist.db"

        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("WHITELIST_DB_PATH", str(db_path))

        from app import main
        from app.whitelist import ProjectWhitelist
        from app.file_store import FileStore

        main.whitelist = ProjectWhitelist(db_path=str(db_path))
        main.file_store = FileStore(data_dir=str(data_dir))

        # Add test project to whitelist
        main.whitelist.add_project("AAA")

        return TestClient(main.app), data_dir

    def test_receive_writes_file_when_whitelisted(
        self, configured_client, valid_message
    ):
        """Test that receiving a message writes it to disk when whitelisted."""
        client, data_dir = configured_client

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 200

        # Verify file was written
        expected_path = (
            data_dir / "incoming" / "2026" / "01" / "30" /
            f"{valid_message['ID']}.json"
        )
        assert expected_path.exists()

        with open(expected_path) as f:
            written_data = json.load(f)
        assert written_data["ID"] == valid_message["ID"]

    def test_receive_returns_500_on_disk_error(
        self, configured_client, valid_message, mocker
    ):
        """Test that disk write errors return 500 with generic error."""
        client, data_dir = configured_client

        mocker.patch(
            "app.main.file_store.write_message",
            side_effect=FileStoreError("Disk full")
        )

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"
        # Should NOT leak disk error details
        assert "disk" not in data["error"].lower()
