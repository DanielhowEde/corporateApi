"""Tests for file writing functionality."""
import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.file_store import FileStore, FileStoreError
from app.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


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


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory."""
    return tmp_path / "data"


class TestFileStore:
    """Tests for FileStore class."""

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

    def test_write_message_is_atomic(self, temp_data_dir, valid_message):
        """Test that no temp files remain after successful write."""
        store = FileStore(data_dir=str(temp_data_dir))

        store.write_message(valid_message)

        # Check no .tmp files remain in tmp directory
        tmp_files = list((temp_data_dir / "tmp").glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_write_message_different_dates(self, temp_data_dir, valid_message):
        """Test that different dates create different directory paths."""
        store = FileStore(data_dir=str(temp_data_dir))

        # First message - 30012026
        valid_message["Date"] = "30012026T11:22:33"
        valid_message["ID"] = str(uuid.uuid4())
        store.write_message(valid_message)

        # Second message - 15062025
        valid_message["Date"] = "15062025T09:00:00"
        valid_message["ID"] = str(uuid.uuid4())
        store.write_message(valid_message)

        assert (temp_data_dir / "incoming" / "2026" / "01" / "30").exists()
        assert (temp_data_dir / "incoming" / "2025" / "06" / "15").exists()

    def test_write_message_handles_multiple_messages_same_day(
        self, temp_data_dir, valid_message
    ):
        """Test that multiple messages on same day work correctly."""
        store = FileStore(data_dir=str(temp_data_dir))

        ids = []
        for i in range(3):
            valid_message["ID"] = str(uuid.uuid4())
            ids.append(valid_message["ID"])
            store.write_message(valid_message)

        day_dir = temp_data_dir / "incoming" / "2026" / "01" / "30"
        files = list(day_dir.glob("*.json"))
        assert len(files) == 3

        for msg_id in ids:
            assert (day_dir / f"{msg_id}.json").exists()


class TestReceiveEndpointFileWriting:
    """Integration tests for /dmz/messages file writing."""

    def test_receive_writes_file_to_disk(
        self, client, valid_message, tmp_path, monkeypatch
    ):
        """Test that receiving a message writes it to disk."""
        # Set up temporary data directory
        data_dir = tmp_path / "data"
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        # Re-initialize file store with new data dir
        from app import main
        main.file_store = FileStore(data_dir=str(data_dir))

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
        self, client, valid_message, mocker
    ):
        """Test that disk write errors return 500 with generic error."""
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

    def test_receive_response_includes_message_id(
        self, client, valid_message, tmp_path, monkeypatch
    ):
        """Test that success response includes the message ID."""
        data_dir = tmp_path / "data"
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        from app import main
        main.file_store = FileStore(data_dir=str(data_dir))

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == valid_message["ID"]


class TestFileStoreErrorHandling:
    """Tests for FileStore error handling."""

    def test_write_to_readonly_directory_raises_error(self, tmp_path, valid_message):
        """Test that writing to read-only directory raises FileStoreError."""
        # Create read-only directory (only works on Unix-like systems)
        if os.name != "nt":  # Skip on Windows
            data_dir = tmp_path / "readonly"
            data_dir.mkdir()
            incoming_dir = data_dir / "incoming"
            incoming_dir.mkdir()
            os.chmod(incoming_dir, 0o444)

            store = FileStore(data_dir=str(data_dir))

            with pytest.raises(FileStoreError):
                store.write_message(valid_message)

            # Cleanup: restore permissions
            os.chmod(incoming_dir, 0o755)
