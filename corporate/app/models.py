"""
Message models and validation for DMZ API.
"""
import re
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MessageData(BaseModel):
    """Flexible data object allowing arbitrary key-value pairs."""
    model_config = {"extra": "allow"}


class Message(BaseModel):
    """
    DMZ Message schema with strict validation.

    Top-level fields are strictly enforced (no extra fields allowed).
    The Data field is flexible and allows arbitrary nested content.
    """
    model_config = {"extra": "forbid"}

    ID: str = Field(..., description="UUID identifier for the message")
    Project: str = Field(..., description="3-character project code (A-Z0-9)")
    TestID: str = Field(..., description="Test identifier")
    Area: str = Field(..., description="Area name")
    Status: str = Field(..., description="Current status")
    Date: str = Field(..., description="Date in ddMMyyyyThh:mm:ss format")
    Data: Dict[str, Any] = Field(..., description="Flexible data object")

    @field_validator("ID")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        """Validate ID is a valid UUID."""
        try:
            uuid.UUID(v)
        except (ValueError, TypeError):
            raise ValueError("Invalid UUID format")
        return v

    @field_validator("Project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        """Validate Project is exactly 3 alphanumeric characters (uppercase)."""
        if not re.match(r"^[A-Z0-9]{3}$", v):
            raise ValueError("Project must be exactly 3 uppercase alphanumeric characters")
        return v

    @field_validator("Date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate Date matches ddMMyyyyThh:mm:ss format exactly."""
        # Pattern: ddMMyyyyThh:mm:ss
        # dd: 01-31, MM: 01-12, yyyy: 4 digits, T literal, hh:mm:ss
        pattern = r"^[0-3][0-9][0-1][0-9][0-9]{4}T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]$"
        if not re.match(pattern, v):
            raise ValueError("Date must match ddMMyyyyThh:mm:ss format")
        return v

    @field_validator("Data")
    @classmethod
    def validate_data_is_dict(cls, v: Any) -> Dict[str, Any]:
        """Ensure Data is a dictionary/object."""
        if not isinstance(v, dict):
            raise ValueError("Data must be an object")
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
