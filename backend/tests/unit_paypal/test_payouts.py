"""Payouts v1: one POST per batch, deterministic ids, duplicates are not errors."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx
from app.services.paypal.payouts import PayoutError, send_payout

BASE = "https://api-m.sandbox.paypal.com"
PAYOUTS_URL = f"{BASE}/v1/payments/payouts"
BATCH = "gameya-circle-golden-01-0"


async def _token() -> str:
    return "test-token"


def _created(batch_id: str = "PB-123", status: str = "PENDING") -> httpx.Response:
    return httpx.Response(
        201, json={"batch_header": {"payout_batch_id": batch_id, "batch_status": status}}
    )


@respx.mock
async def test_payout_posts_once_with_deterministic_ids() -> None:
    route = respx.post(PAYOUTS_URL).mock(return_value=_created())
    result = await send_payout(
        sender_batch_id=BATCH,
        receiver_email="amina-buyer@gameya-sandbox.dev",
        amount=Decimal("600"),
        token_provider=_token,
    )
    assert (result.batch_id, result.status) == ("PB-123", "PENDING")
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["PayPal-Request-Id"] == BATCH
    assert request.headers["Authorization"] == "Bearer test-token"
    body = json.loads(request.content)
    assert body["sender_batch_header"]["sender_batch_id"] == BATCH
    item = body["items"][0]
    assert item["amount"] == {"value": "600.00", "currency": "USD"}
    assert item["receiver"] == "amina-buyer@gameya-sandbox.dev"
    assert item["recipient_type"] == "EMAIL"


@respx.mock
async def test_duplicate_batch_id_is_not_an_error() -> None:
    respx.post(PAYOUTS_URL).mock(
        return_value=httpx.Response(
            400,
            json={"name": "SENDER_BATCH_ID_ALREADY_USED", "debug_id": "dbg-1"},
        )
    )
    result = await send_payout(
        sender_batch_id=BATCH,
        receiver_email="amina-buyer@gameya-sandbox.dev",
        amount=Decimal("600.00"),
        token_provider=_token,
    )
    assert result.status == "ALREADY_SENT"


@respx.mock
async def test_other_errors_raise_without_secrets() -> None:
    respx.post(PAYOUTS_URL).mock(
        return_value=httpx.Response(
            403,
            json={"name": "AUTHORIZATION_ERROR", "debug_id": "dbg-2"},
            headers={"PayPal-Debug-Id": "dbg-2"},
        )
    )
    with pytest.raises(PayoutError) as info:
        await send_payout(
            sender_batch_id=BATCH,
            receiver_email="amina-buyer@gameya-sandbox.dev",
            amount=Decimal("600.00"),
            token_provider=_token,
        )
    assert info.value.status == 403
    assert "test-token" not in str(info.value)
    assert "amina" not in str(info.value)


async def test_amount_must_be_positive() -> None:
    with pytest.raises(ValueError):
        await send_payout(
            sender_batch_id=BATCH,
            receiver_email="a@b.c",
            amount=Decimal("0"),
            token_provider=_token,
        )
