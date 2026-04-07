"""
HTTP client for wrapping messages with JWT via the Certificate Gateway.

The certificate gateway is an external service that:
1. Receives a message payload
2. Wraps it with a signed JWT token
3. Returns the JWT-wrapped message

Messages must be cert-wrapped before being sent to the DMZ Gateway.
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from .utils import get_request_id, setup_logging

logger = setup_logging("cert_client")


class CertGatewayError(Exception):
    """Exception raised when certificate gateway communication fails."""
    pass


class CertGatewayUnavailableError(CertGatewayError):
    """Exception raised when the certificate gateway is unavailable."""
    pass


class CertClient:
    """
    Async HTTP client for communicating with the Certificate Gateway.

    Sends messages to the cert gateway to be wrapped with a JWT token
    before forwarding to the DMZ Gateway.
    """

    DEFAULT_TIMEOUT = 30.0
    MAX_RETRIES = 2
    INITIAL_BACKOFF = 0.5

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT
    ):
        from .config import config
        self.base_url = base_url or config.cert_gateway_url
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout)
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def wrap_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a message to the certificate gateway to be wrapped with a JWT.

        The cert gateway returns the original message wrapped in a JWT envelope:
        {
            "token": "<signed JWT containing the message>",
            "expires_at": "<ISO 8601 expiry timestamp>",
            "message": {<original message data>}
        }

        Args:
            message_data: The message payload to wrap

        Returns:
            Dict containing the JWT-wrapped message with token and expiry

        Raises:
            CertGatewayUnavailableError: If cert gateway is unavailable after retries
            CertGatewayError: For other cert gateway errors
        """
        request_id = get_request_id()
        message_id = message_data.get("ID", "unknown")

        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"Requesting cert wrap: message_id={message_id}, "
                    f"attempt={attempt + 1}/{self.MAX_RETRIES + 1}"
                )

                response = await client.post(
                    "/wrap",
                    json=message_data,
                    headers={"X-Request-ID": request_id}
                )

                if response.status_code >= 500:
                    logger.warning(
                        f"Cert gateway returned {response.status_code}: message_id={message_id}"
                    )
                    last_error = CertGatewayError(f"Cert gateway returned {response.status_code}")

                    if attempt < self.MAX_RETRIES:
                        backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        raise CertGatewayUnavailableError(
                            f"Cert gateway unavailable after {self.MAX_RETRIES + 1} attempts"
                        )

                if response.status_code >= 400:
                    logger.error(
                        f"Cert gateway rejected message: message_id={message_id}, "
                        f"status={response.status_code}"
                    )
                    raise CertGatewayError(f"Cert gateway rejected message: {response.status_code}")

                wrapped = response.json()
                logger.info(f"Message cert-wrapped successfully: message_id={message_id}")
                return wrapped

            except httpx.TimeoutException as e:
                logger.warning(f"Cert gateway timeout: message_id={message_id}, attempt={attempt + 1}")
                last_error = e
                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

            except httpx.ConnectError as e:
                logger.warning(f"Cert gateway connection error: message_id={message_id}, attempt={attempt + 1}")
                last_error = e
                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

        logger.error(
            f"Cert gateway unavailable after all retries: message_id={message_id}, "
            f"last_error={last_error}"
        )
        raise CertGatewayUnavailableError(
            f"Cert gateway unavailable after {self.MAX_RETRIES + 1} attempts"
        )

    @staticmethod
    def is_cert_expired(wrapped_message: Dict[str, Any]) -> bool:
        """
        Check if the JWT certificate on a wrapped message has expired.

        Args:
            wrapped_message: The cert-wrapped message containing 'expires_at'

        Returns:
            True if expired or no expiry found, False if still valid
        """
        expires_at = wrapped_message.get("expires_at")
        if not expires_at:
            return True
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= expiry
        except (ValueError, TypeError):
            logger.warning(f"Invalid expires_at format: {expires_at}")
            return True

    @staticmethod
    def seconds_until_expiry(wrapped_message: Dict[str, Any]) -> float:
        """
        Get seconds remaining until the cert expires.

        Args:
            wrapped_message: The cert-wrapped message containing 'expires_at'

        Returns:
            Seconds until expiry (0 if already expired or invalid)
        """
        expires_at = wrapped_message.get("expires_at")
        if not expires_at:
            return 0
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            return max(0, remaining)
        except (ValueError, TypeError):
            return 0
