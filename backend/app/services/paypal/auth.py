"""PayPal OAuth2 client-credentials token manager (plan REQ-PP-01, section 4.4).

The token is cached and refreshed 300 s before it expires; concurrent callers share
one request. `PayPalAuth.token` is the `token_provider` every PayPal caller uses
(the agents' REST executor and the payouts client). Errors carry the HTTP status and
PayPal's debug id only: never the client id, the secret or a token.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from functools import cache

import httpx

from ...config import get_settings

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
REFRESH_MARGIN_S = 300.0
REQUEST_TIMEOUT_S = 15.0


class PayPalAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = SANDBOX_BASE_URL,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = base_url.rstrip("/") + "/v1/oauth2/token"
        self._client = client
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0

    async def token(self) -> str:
        """A valid access token, fetched only when missing or close to expiry."""
        if self._token is not None and self._clock() < self._refresh_at:
            return self._token
        async with self._lock:
            # Another caller may have refreshed while this one waited for the lock.
            if self._token is not None and self._clock() < self._refresh_at:
                return self._token
            token, expires_in = await self._fetch()
            self._token = token
            self._refresh_at = self._clock() + max(expires_in - REFRESH_MARGIN_S, 0.0)
            return token

    def invalidate(self) -> None:
        """Drop the cached token (call after PayPal answers 401)."""
        self._token = None
        self._refresh_at = 0.0

    async def _fetch(self) -> tuple[str, float]:
        client = self._client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
        try:
            response = await client.post(
                self._token_url,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"PayPal token request failed ({type(exc).__name__})") from None
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code != 200:
            debug_id = response.headers.get("PayPal-Debug-Id", "none")
            raise RuntimeError(
                f"PayPal token request returned {response.status_code} (debug_id {debug_id})"
            )
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("PayPal token response had no access token")
        return token, float(body.get("expires_in", 0))


@cache
def get_paypal_auth() -> PayPalAuth:
    """The shared token manager, built from settings; raises if credentials are missing."""
    settings = get_settings()
    if not (settings.paypal_client_id and settings.paypal_client_secret):
        raise RuntimeError("PayPal credentials are not configured")
    return PayPalAuth(
        settings.paypal_client_id,
        settings.paypal_client_secret,
        settings.paypal_api_base_url or SANDBOX_BASE_URL,
    )
