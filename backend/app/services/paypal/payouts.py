"""PayPal Payouts v1 (sandbox): the only code that sends money out.

Called only by `domain/circle_engine.close_cycle`, after the ledger invariants pass.
No module under `app/ai` may import this (plan gate G3, enforced by a test).

Idempotency: `sender_batch_id` is deterministic per (circle, cycle) and is also sent as
`PayPal-Request-Id`, so a retried POST cannot create a second batch. If PayPal says the
batch id was already used, the money already went out: that is reported, not raised.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import httpx

from ...config import get_settings
from .auth import SANDBOX_BASE_URL

PAYOUTS_PATH = "/v1/payments/payouts"
REQUEST_TIMEOUT_S = 20.0
ALREADY_SENT = "ALREADY_SENT"


@dataclass(frozen=True)
class PayoutResult:
    batch_id: str | None
    status: str  # PayPal batch_status (PENDING, PROCESSING, SUCCESS, ...) or ALREADY_SENT


class PayoutError(RuntimeError):
    """The payout POST failed. Carries only the HTTP status and PayPal debug id."""

    def __init__(self, status: int, debug_id: str | None) -> None:
        super().__init__(f"PayPal payout failed: status={status} debug_id={debug_id}")
        self.status = status
        self.debug_id = debug_id


def _base_url() -> str:
    return (get_settings().paypal_api_base_url or SANDBOX_BASE_URL).rstrip("/")


async def send_payout(
    *,
    sender_batch_id: str,
    receiver_email: str,
    amount: Decimal,
    token_provider: Callable[[], Awaitable[str]],
    client: httpx.AsyncClient | None = None,
) -> PayoutResult:
    """POST one single-item payout batch; returns PayPal's batch id and status."""
    value = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        raise ValueError("a payout amount must be positive")
    body = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "Your Gam'eya payout has arrived",
            "email_message": "Your savings circle paid you this cycle's pot.",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": f"{value:.2f}", "currency": "USD"},
                "receiver": receiver_email,
                "note": "Gam'eya savings circle payout",
                "sender_item_id": sender_batch_id,
            }
        ],
    }
    try:
        token = await token_provider()
    except Exception:
        raise PayoutError(0, None) from None  # provider errors may carry credentials
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": sender_batch_id,
    }
    owned = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    try:
        response = await http.post(_base_url() + PAYOUTS_PATH, json=body, headers=headers)
    except httpx.HTTPError:
        raise PayoutError(0, None) from None
    finally:
        if owned:
            await http.aclose()

    payload: object
    try:
        payload = response.json()
    except ValueError:
        payload = None
    data = payload if isinstance(payload, dict) else {}
    if response.status_code < 400:
        header = data.get("batch_header")
        if isinstance(header, dict):
            batch_id = header.get("payout_batch_id")
            status = header.get("batch_status")
            return PayoutResult(
                batch_id=batch_id if isinstance(batch_id, str) else None,
                status=status if isinstance(status, str) else "PENDING",
            )
        raise PayoutError(response.status_code, response.headers.get("PayPal-Debug-Id"))
    if data.get("name") == "SENDER_BATCH_ID_ALREADY_USED":
        return PayoutResult(batch_id=None, status=ALREADY_SENT)
    debug_id = response.headers.get("PayPal-Debug-Id") or (
        data.get("debug_id") if isinstance(data.get("debug_id"), str) else None
    )
    raise PayoutError(response.status_code, debug_id)
