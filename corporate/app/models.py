"""
Message models and validation for DMZ API.
"""
import re
import uuid
from datetime import datetime
from typing import Dict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Message(BaseModel):
    """
    DMZ Message schema with strict validation.

    Top-level fields are strictly enforced (no extra fields allowed).
    Field aliases map Python identifiers to the JSON keys with spaces.

    Use Message.model_validate(dict) to parse (not Message(**dict))
    Use .model_dump(by_alias=True) to serialize back to original JSON keys.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ID: str = Field(..., description="UUID identifier for the message")
    Project: str = Field(..., description="3-character project code (A-Z0-9)")
    test_id: str = Field(..., alias="Test ID", description="Test identifier (3-10 chars)")
    Timestamp: str = Field(..., description="ISO 8601 datetime (e.g. 2026-01-30T11:22:33)")
    test_status: str = Field(..., alias="Test Status", description="Current test status")
    Data: Dict[str, str] = Field(..., description="String key-value pairs (max 20 entries)")

    @field_validator("ID")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, TypeError):
            raise ValueError("Invalid UUID format")
        return v

    @field_validator("Project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9]{3}$", v):
            raise ValueError("Project must be exactly 3 uppercase alphanumeric characters")
        return v

    @field_validator("test_id")
    @classmethod
    def validate_test_id(cls, v: str) -> str:
        if not (3 <= len(v) <= 10):
            raise ValueError("Test ID must be 3-10 characters")
        return v

    @field_validator("Timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("Timestamp must be a valid ISO 8601 datetime (e.g. 2026-01-30T11:22:33)")
        return v

    @field_validator("Data")
    @classmethod
    def validate_data(cls, v: Dict[str, str]) -> Dict[str, str]:
        if not isinstance(v, dict):
            raise ValueError("Data must be an object")
        if len(v) > 20:
            raise ValueError("Data must not have more than 20 properties")
        pattern = re.compile(r"^[a-zA-Z0-9 ;]+$")
        for key, value in v.items():
            if not isinstance(value, str):
                raise ValueError(f"Data values must be strings (key: '{key}')")
            if not (1 <= len(value) <= 128):
                raise ValueError(f"Data['{key}'] must be 1-128 characters")
            if not pattern.match(value):
                raise ValueError(
                    f"Data['{key}'] contains invalid characters (allowed: a-z A-Z 0-9 space ;)"
                )
        return v


class SuccessResponse(BaseModel):
    """Successful response model."""
    success: bool = True
    request_id: str
    message_id: str


class ErrorResponse(BaseModel):
    """Generic error response model."""
    success: bool = False
    request_id: str
    error: str = "Invalid request"


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
