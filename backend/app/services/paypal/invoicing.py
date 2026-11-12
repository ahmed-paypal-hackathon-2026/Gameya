"""Engine-owned PayPal Invoicing v2: open the cycle's invoices and read their status.

This is deterministic engine code, not an AI path: no model writes these invoices and
no agent can reach this module (a test forbids `app/ai` from importing it). Creating
and sending reuse the sandbox REST executor (same body shape, retries and request
ids); reading status is a plain GET the agent toolkit does not offer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from ...ai.paypal_rest import PayPalToolError, RestToolExecutor
from ...ai.toolkit_adapter import CreateInvoiceParameters, SendInvoiceParameters, request_id
from .auth import SANDBOX_BASE_URL, get_paypal_auth

INVOICES_PATH = "/v2/invoicing/invoices"
REQUEST_TIMEOUT_S = 20.0
# Only money PayPal received counts. MARKED_AS_PAID is the merchant's own claim (e.g.
# cash), so it never releases a payout.
PAID_STATUSES = frozenset({"PAID"})


class InvoiceStatusError(RuntimeError):
    """Reading an invoice failed. Carries only the HTTP status and PayPal debug id."""

    def __init__(self, status: int, debug_id: str | None) -> None:
        super().__init__(f"PayPal invoice read failed: status={status} debug_id={debug_id}")
        self.status = status
        self.debug_id = debug_id


class InvoiceGateway(Protocol):
    async def create_and_send(self, params: CreateInvoiceParameters, *, key: str) -> str:
        """Create the invoice, send it, return its PayPal id. `key` fixes the request ids."""
        ...

    async def get_status(self, invoice_id: str) -> str:
        """PayPal's status for the invoice (DRAFT, SENT, PAID, MARKED_AS_PAID, ...)."""
        ...


class PayPalInvoicing:
    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._client = client
        self._rest = RestToolExecutor(token_provider=token_provider, client=client)

    async def aclose(self) -> None:
        await self._rest.aclose()

    async def create_and_send(self, params: CreateInvoiceParameters, *, key: str) -> str:
        created: dict[str, Any] = await self._rest.call(
            "create_invoice", params, request_id=request_id(key, "create_invoice")
        )
        invoice_id = created.get("id")
        if not isinstance(invoice_id, str) or not invoice_id:
            raise PayPalToolError("create_invoice", 0, None)
        await self._rest.call(
            "send_invoice",
            SendInvoiceParameters(invoice_id=invoice_id),
            request_id=request_id(key, "send_invoice"),
        )
        return invoice_id

    async def get_status(self, invoice_id: str) -> str:
        try:
            token = await self._token_provider()
        except Exception:
            raise InvoiceStatusError(0, None) from None
        url = f"{SANDBOX_BASE_URL}{INVOICES_PATH}/{quote(invoice_id, safe='')}"
        owned = self._client is None
        http = self._client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
        try:
            response = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            raise InvoiceStatusError(0, None) from None
        finally:
            if owned:
                await http.aclose()
        if response.status_code >= 400:
            raise InvoiceStatusError(response.status_code, response.headers.get("PayPal-Debug-Id"))
        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            status = None
        if not isinstance(status, str):
            raise InvoiceStatusError(response.status_code, response.headers.get("PayPal-Debug-Id"))
        return status


async def get_invoice_gateway() -> AsyncIterator[InvoiceGateway]:
    """FastAPI dependency: the sandbox invoicing gateway, or 503 without credentials."""
    try:
        auth = get_paypal_auth()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="PayPal sandbox credentials are not configured"
        ) from exc
    gateway = PayPalInvoicing(token_provider=auth.token)
    try:
        yield gateway
    finally:
        await gateway.aclose()


def get_token_provider() -> Callable[[], Awaitable[str]]:
    """FastAPI dependency: the cached OAuth token for engine calls (503 if unconfigured)."""
    try:
        return get_paypal_auth().token
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="PayPal sandbox credentials are not configured"
        ) from exc
