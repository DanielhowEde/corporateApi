"""
HTTP client for forwarding messages to the DMZ Gateway.
"""

import asyncio
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
        self, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT
    ):
        """
        Initialize the gateway client.

        Args:
            base_url: Gateway base URL. If not provided, reads from GATEWAY_URL env var.
            timeout: Request timeout in seconds
        """
        from .config import config

        self.base_url = base_url or config.gateway_url
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(self.timeout)
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def sync_ca(self, ca_pem: str) -> None:
        """
        Send the corporate CA certificate to low-side via gateway (best-effort).

        Low-side stores this and uses it as the trust root for incoming mTLS
        connections — so client certs issued by corporate are also accepted
        by low-side.

        Args:
            ca_pem: CA certificate in PEM format
        """
        payload = {"ca_pem": ca_pem}
        try:
            client = await self._get_client()
            response = await client.post(
                "/ca", json=payload, headers={"X-Request-ID": get_request_id()}
            )
            if response.status_code < 300:
                logger.info("CA cert synced to gateway")
            else:
                logger.warning(f"Gateway returned {response.status_code} for CA sync")
        except Exception as e:
            logger.warning(f"CA sync to gateway failed (non-fatal): {e}")

    async def sync_client_cert(self, cert_data: Dict[str, Any]) -> None:
        """
        Send an issued client cert's public info to low-side (best-effort).

        Only the public certificate PEM and metadata are sent — never the
        private key. Low-side stores this as an audit/allowlist record.

        Args:
            cert_data: Dict with key_id, name, cert_pem, action (upsert|revoke|delete)
        """
        key_id = cert_data.get("key_id", "unknown")
        try:
            client = await self._get_client()
            response = await client.post(
                "/client-certs",
                json=cert_data,
                headers={"X-Request-ID": get_request_id()},
            )
            if response.status_code < 300:
                logger.info(f"Client cert synced to gateway: key_id={key_id}")
            else:
                logger.warning(
                    f"Gateway returned {response.status_code} for cert sync: {key_id}"
                )
        except Exception as e:
            logger.warning(f"Client cert sync to gateway failed (non-fatal): {e}")

    async def sync_user(self, user_data: Dict[str, Any]) -> None:
        """
        Send a user sync event to the DMZ Gateway (best-effort, no retry).

        Called by admin routes when a user is created, updated, enabled,
        disabled, or deleted. Failures are logged but not raised.

        Args:
            user_data: Dict with username, password_hash, enabled, must_change_password, action
        """
        username = user_data.get("username", "unknown")
        try:
            client = await self._get_client()
            response = await client.post(
                "/users", json=user_data, headers={"X-Request-ID": get_request_id()}
            )
            if response.status_code < 300:
                logger.info(f"User sync sent to gateway: {username}")
            else:
                logger.warning(
                    f"Gateway returned {response.status_code} for user sync: {username}"
                )
        except Exception as e:
            logger.warning(
                f"User sync to gateway failed (non-fatal): username={username}, error={e}"
            )

    def set_cert_client(self, cert_client) -> None:
        """Set the certificate client for JWT wrapping before send."""
        self._cert_client = cert_client

    async def send_message(
        self, message_data: Dict[str, Any], auto_send: bool = True
    ) -> Dict[str, Any]:
        """
        Send a message to the DMZ Gateway.

        If a cert client is configured, the message is first sent to the
        certificate gateway to be wrapped with a JWT. If auto_send is False,
        the wrapped message is returned without forwarding to the gateway
        (the caller is responsible for sending it later).

        Implements retry logic with exponential backoff for:
        - Connection errors
        - Timeout errors
        - 5xx server errors

        Args:
            message_data: The message to send
            auto_send: If True (default), send immediately after cert wrapping.
                       If False, return the cert-wrapped message without sending.

        Returns:
            Response data from the gateway (or wrapped message if auto_send=False)

        Raises:
            GatewayUnavailableError: If gateway is unavailable after retries
            GatewayError: For other gateway communication errors
        """
        request_id = get_request_id()
        message_id = message_data.get("ID", "unknown")

        # Cert-wrap the message if cert client is available
        payload = message_data
        cert_client = getattr(self, "_cert_client", None)
        if cert_client:

            logger.info(f"Requesting cert wrap for message: message_id={message_id}")
            wrapped = await cert_client.wrap_message(message_data)
            payload = wrapped

            if not auto_send:
                logger.info(
                    f"Auto-send disabled, returning wrapped message: message_id={message_id}"
                )
                return {
                    "status": "pending",
                    "message_id": message_id,
                    "wrapped": wrapped,
                }

            # Wait for cert expiry if needed (cert must be valid when sent)
            remaining = cert_client.seconds_until_expiry(wrapped)
            if remaining > 0:
                logger.info(
                    f"Cert still valid for {remaining:.1f}s, sending immediately: "
                    f"message_id={message_id}"
                )

        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"Sending message to gateway: message_id={message_id}, "
                    f"attempt={attempt + 1}/{self.MAX_RETRIES + 1}"
                )

                response = await client.post(
                    "/messages", json=payload, headers={"X-Request-ID": request_id}
                )

                # Check for 5xx errors (retry these)
                if response.status_code >= 500:
                    logger.warning(
                        f"Gateway returned {response.status_code}: message_id={message_id}"
                    )
                    last_error = GatewayError(
                        f"Gateway returned {response.status_code}"
                    )

                    if attempt < self.MAX_RETRIES:
                        backoff = self.INITIAL_BACKOFF * (2**attempt)
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
                    raise GatewayError(
                        f"Gateway rejected message: {response.status_code}"
                    )

                # Success
                logger.info(f"Message sent successfully: message_id={message_id}")
                return response.json()

            except httpx.TimeoutException as e:
                logger.warning(
                    f"Gateway timeout: message_id={message_id}, attempt={attempt + 1}"
                )
                last_error = e

                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2**attempt)
                    await asyncio.sleep(backoff)
                    continue

            except httpx.ConnectError as e:
                logger.warning(
                    f"Gateway connection error: message_id={message_id}, attempt={attempt + 1}"
                )
                last_error = e

                if attempt < self.MAX_RETRIES:
                    backoff = self.INITIAL_BACKOFF * (2**attempt)
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

    async def send_wrapped(self, wrapped_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send an already cert-wrapped message to the DMZ Gateway.

        Used for releasing messages from the pending queue.

        Args:
            wrapped_payload: The cert-wrapped message envelope

        Returns:
            Response data from the gateway

        Raises:
            GatewayUnavailableError: If gateway is unavailable after retries
            GatewayError: For other gateway communication errors
        """
        request_id = get_request_id()
        message_id = wrapped_payload.get("message", {}).get("ID", "unknown")

        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"Sending wrapped message to gateway: message_id={message_id}, "
                    f"attempt={attempt + 1}/{self.MAX_RETRIES + 1}"
                )

                response = await client.post(
                    "/messages",
                    json=wrapped_payload,
                    headers={"X-Request-ID": request_id},
                )

                if response.status_code >= 500:
                    last_error = GatewayError(
                        f"Gateway returned {response.status_code}"
                    )
                    if attempt < self.MAX_RETRIES:
                        backoff = self.INITIAL_BACKOFF * (2**attempt)
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        raise GatewayUnavailableError(
                            f"Gateway unavailable after {self.MAX_RETRIES + 1} attempts"
                        )

                if response.status_code >= 400:
                    raise GatewayError(
                        f"Gateway rejected message: {response.status_code}"
                    )

                logger.info(
                    f"Wrapped message sent successfully: message_id={message_id}"
                )
                return response.json()

            except httpx.TimeoutException:
                last_error = GatewayError("Timeout")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.INITIAL_BACKOFF * (2**attempt))
                    continue

            except httpx.ConnectError:
                last_error = GatewayError("Connection error")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.INITIAL_BACKOFF * (2**attempt))
                    continue
                else:
                    print("Gateway connection error: ",last_error)


        raise GatewayUnavailableError(
            f"Gateway unavailable after {self.MAX_RETRIES + 1} attempts"
        )
