"""PayPal OAuth token manager: cached, refreshed early, single-flight, secret-safe."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
import respx
from app.config import get_settings
from app.services.paypal.auth import PayPalAuth, get_paypal_auth

BASE = "https://api-m.sandbox.paypal.com"
TOKEN_URL = f"{BASE}/v1/oauth2/token"
CLIENT_ID = "client-id-123"
SECRET = "very-secret-value"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _ok(token: str, expires_in: int = 32400) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


def _auth(clock: Clock | None = None) -> PayPalAuth:
    return PayPalAuth(CLIENT_ID, SECRET, BASE, clock=clock or Clock())


@respx.mock
async def test_token_is_fetched_once_and_cached() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=_ok("A"))
    auth = _auth()
    assert await auth.token() == "A"
    assert await auth.token() == "A"
    assert route.call_count == 1
    request = route.calls[0].request
    expected = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert request.content == b"grant_type=client_credentials"


@respx.mock
async def test_token_refreshes_300_seconds_before_expiry() -> None:
    route = respx.post(TOKEN_URL).mock(side_effect=[_ok("A", 1000), _ok("B", 1000)])
    clock = Clock()
    auth = _auth(clock)
    assert await auth.token() == "A"
    clock.now = 699
    assert await auth.token() == "A"
    clock.now = 701
    assert await auth.token() == "B"
    assert route.call_count == 2


@respx.mock
async def test_invalidate_forces_a_new_token() -> None:
    route = respx.post(TOKEN_URL).mock(side_effect=[_ok("A"), _ok("B")])
    auth = _auth()
    assert await auth.token() == "A"
    auth.invalidate()
    assert await auth.token() == "B"
    assert route.call_count == 2


@respx.mock
async def test_concurrent_callers_share_one_request() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=_ok("A"))
    auth = _auth()
    tokens = await asyncio.gather(*(auth.token() for _ in range(5)))
    assert tokens == ["A"] * 5
    assert route.call_count == 1


@respx.mock
async def test_failure_raises_without_leaking_the_secret() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"error": "invalid_client"}, headers={"PayPal-Debug-Id": "dbg-42"}
        )
    )
    with pytest.raises(RuntimeError) as caught:
        await _auth().token()
    message = str(caught.value)
    assert SECRET not in message and CLIENT_ID not in message
    assert "401" in message and "dbg-42" in message


@respx.mock
async def test_a_response_without_a_token_is_an_error() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"expires_in": 100}))
    with pytest.raises(RuntimeError, match="no access token"):
        await _auth().token()


def test_get_paypal_auth_needs_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_API_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    get_paypal_auth.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="not configured"):
            get_paypal_auth()
    finally:
        get_settings.cache_clear()
        get_paypal_auth.cache_clear()


def test_get_paypal_auth_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("PAYPAL_API_BASE_URL", BASE)
    get_settings.cache_clear()
    get_paypal_auth.cache_clear()
    try:
        assert get_paypal_auth() is get_paypal_auth()
    finally:
        get_settings.cache_clear()
        get_paypal_auth.cache_clear()
