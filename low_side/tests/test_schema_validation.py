"""Tests for message schema validation."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Message


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


class TestMessageModel:
    """Tests for the Message Pydantic model."""

    def test_valid_message_passes_validation(self, valid_message):
        """Test that a valid message passes validation."""
        message = Message(**valid_message)
        assert message.ID == valid_message["ID"]
        assert message.Project == "AAA"

    def test_invalid_uuid_fails_validation(self, valid_message):
        """Test that invalid UUID fails validation."""
        valid_message["ID"] = "not-a-uuid"
        with pytest.raises(ValueError):
            Message(**valid_message)

    def test_project_must_be_3_chars(self, valid_message):
        """Test that Project must be exactly 3 characters."""
        valid_message["Project"] = "AB"
        with pytest.raises(ValueError):
            Message(**valid_message)

        valid_message["Project"] = "ABCD"
        with pytest.raises(ValueError):
            Message(**valid_message)

    def test_project_must_be_uppercase_alphanumeric(self, valid_message):
        """Test that Project must be uppercase alphanumeric."""
        valid_message["Project"] = "abc"
        with pytest.raises(ValueError):
            Message(**valid_message)

        valid_message["Project"] = "A-B"
        with pytest.raises(ValueError):
            Message(**valid_message)

    def test_project_allows_alphanumeric(self, valid_message):
        """Test that Project allows uppercase letters and numbers."""
        valid_message["Project"] = "A1B"
        message = Message(**valid_message)
        assert message.Project == "A1B"

        valid_message["Project"] = "123"
        message = Message(**valid_message)
        assert message.Project == "123"

    def test_date_must_match_format(self, valid_message):
        """Test that Date must match ddMMyyyyThh:mm:ss format."""
        # Invalid formats
        invalid_dates = [
            "2026-01-30T11:22:33",  # ISO format
            "30-01-2026T11:22:33",  # Wrong separator
            "30012026 11:22:33",    # Space instead of T
            "3012026T11:22:33",     # Missing digit
            "30012026T11:22",       # Missing seconds
        ]
        for invalid_date in invalid_dates:
            valid_message["Date"] = invalid_date
            with pytest.raises(ValueError):
                Message(**valid_message)

    def test_date_valid_format(self, valid_message):
        """Test that valid date format passes."""
        valid_message["Date"] = "01122025T00:00:00"
        message = Message(**valid_message)
        assert message.Date == "01122025T00:00:00"

    def test_data_must_be_object(self, valid_message):
        """Test that Data must be a dictionary/object."""
        valid_message["Data"] = "not an object"
        with pytest.raises(ValueError):
            Message(**valid_message)

        valid_message["Data"] = ["list", "not", "allowed"]
        with pytest.raises(ValueError):
            Message(**valid_message)

    def test_data_allows_arbitrary_keys(self, valid_message):
        """Test that Data allows arbitrary nested content."""
        valid_message["Data"] = {
            "key1": "value1",
            "key2": 123,
            "nested": {"a": "b"},
            "list": [1, 2, 3]
        }
        message = Message(**valid_message)
        assert message.Data["key1"] == "value1"
        assert message.Data["nested"]["a"] == "b"

    def test_extra_top_level_fields_rejected(self, valid_message):
        """Test that extra top-level fields are rejected."""
        valid_message["ExtraField"] = "should fail"
        with pytest.raises(ValueError):
            Message(**valid_message)

    def test_missing_required_field_fails(self, valid_message):
        """Test that missing required fields fail validation."""
        del valid_message["Project"]
        with pytest.raises(ValueError):
            Message(**valid_message)


class TestSendMessageEndpoint:
    """Tests for POST /messages endpoint schema validation."""

    def test_valid_message_accepted(self, client, valid_message, mocker):
        """Test that valid message schema is accepted."""
        # Mock the gateway client to avoid actual HTTP calls
        mocker.patch(
            "app.main.gateway_client.send_message",
            return_value={"success": True}
        )

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "request_id" in data
        assert data["message_id"] == valid_message["ID"]

    def test_invalid_uuid_returns_400(self, client, valid_message):
        """Test that invalid UUID returns 400 with generic error."""
        valid_message["ID"] = "not-a-uuid"

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"
        assert "request_id" in data

    def test_invalid_project_returns_400(self, client, valid_message):
        """Test that invalid Project returns 400 with generic error."""
        valid_message["Project"] = "invalid"

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"

    def test_extra_fields_returns_400(self, client, valid_message):
        """Test that extra top-level fields return 400."""
        valid_message["UnknownField"] = "should reject"

        response = client.post("/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False

    def test_error_response_does_not_leak_details(self, client, valid_message):
        """Test that error responses don't reveal validation details."""
        valid_message["ID"] = "not-a-uuid"

        response = client.post("/messages", json=valid_message)

        data = response.json()
        # Should NOT contain specific validation error details
        assert "uuid" not in data["error"].lower()
        assert "validation" not in data["error"].lower()


class TestReceiveMessageEndpoint:
    """Tests for POST /dmz/messages endpoint schema validation."""

    def test_valid_message_accepted(self, client, valid_message, tmp_path, mocker):
        """Test that valid message schema is accepted."""
        # Mock the file store
        mocker.patch.object(
            client.app.state if hasattr(client.app, 'state') else type('', (), {})(),
            'file_store',
            create=True
        )
        mocker.patch(
            "app.main.file_store.write_message",
            return_value=tmp_path / "test.json"
        )

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message_id"] == valid_message["ID"]

    def test_invalid_schema_returns_400(self, client, valid_message):
        """Test that invalid schema returns 400 with generic error."""
        valid_message["Project"] = "INVALID"

        response = client.post("/dmz/messages", json=valid_message)

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "Invalid request"
