"""
HTTP client for forwarding messages to the DMZ Gateway.
"""
import asyncio
import os
from typing import Any, Dict, Optional

import httpx

from .utils import get_request_id, setup_logging

logger = setup_logging("gateway_client")


class GatewayError(Exception):
    """Exception raised when gateway communication fails."""
    pass


class GatewayUnavailableError(GatewayError):
    """Exception raised when the gateway is unavailable."""
    pass


class GatewayClient:
    """
    Async HTTP client for communicating with the DMZ Gateway.

    Features:
    - Configurable base URL via environment variable
    - Automatic retry on timeout and 5xx errors (2 retries)
    - Exponential backoff between retries
    """

    DEFAULT_TIMEOUT = 30.0
    MAX_RETRIES = 2
    INITIAL_BACKOFF = 0.5  # seconds

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT
    ):
        """
        Initialize the gateway client.

        Args:
            base_url: Gateway base URL. If not provided, reads from GATEWAY_URL env var.
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.environ.get("GATEWAY_URL", "http://localhost:8080")
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

    async def send_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a message to the DMZ Gateway.

        Implements retry logic with exponential backoff for:
        - Connection errors
        - Timeout errors
        - 5xx server errors

        Args:
            message_data: The message to send

        Returns:
            Response data from the gateway

        Raises:
            GatewayUnavailableError: If gateway is unavailable after retries
            GatewayError: For other gateway communication errors
        """
        request_id = get_request_id()
        message_id = message_data.get("ID", "unknown")

        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"Sending message to gateway: message_id={message_id}, "
                    f"attempt={attempt + 1}/{self.MAX_RETRIES + 1}"
                )

                response = await client.post(
                    "/messages",
                    json=message_data,
                    headers={"X-Request-ID": request_id}
                )

                # Check for 5xx errors (retry these)
                if response.status_code >= 500:
                    logger.warning(
                        f"Gateway returned {response.status_code}: message_id={message_id}"
                    )
                    last_error = GatewayError(f"Gateway returned {response.status_code}")

                    if attempt < self.MAX_RETRIES:
                        backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        raise GatewayUnavailableError(
                            f"Gateway unavailable after {self.MAX_RETRIES + 1} attempts"
                        )

                # Check for 4xx errors (don't retry these)
                if response.status_code >= 400:
                    logger.error(
                        f"Gateway rejected message: message_id={message_id}, "
                        f"status={response.status_code}"
                    )
                    raise GatewayError(f"Gateway rejected message: {response.status_code}")

                # Success
                logger.info(f"Message sent successfully: message_id={message_id}")
                return response.json()

            except httpx.TimeoutException as e:
                logger.warning(f"Gateway timeout: message_id={message_id}, attempt={attempt + 1}")
                last_error = e

                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

            except httpx.ConnectError as e:
                logger.warning(f"Gateway connection error: message_id={message_id}, attempt={attempt + 1}")
                last_error = e

                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue

        # All retries exhausted
        logger.error(
            f"Gateway unavailable after all retries: message_id={message_id}, "
            f"last_error={last_error}"
        )
        raise GatewayUnavailableError(
            f"Gateway unavailable after {self.MAX_RETRIES + 1} attempts"
        )
