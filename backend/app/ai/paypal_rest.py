"""Sandbox-only REST implementation of `ToolExecutor`.

Performs the toolkit-shaped calls (`create_product`, `create_subscription_plan`,
`create_invoice`, `send_invoice`, `send_invoice_reminder`,
`generate_invoice_qr_code`) directly against the PayPal sandbox. No Payouts
endpoint is reachable from here. The access token comes from an injected
provider (Person 1's auth manager); this module never reads credentials.

Logging policy: tokens, headers and request bodies are never logged or put in
an error. `PayPalToolError` carries only the tool, HTTP status and debug id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
REQUEST_TIMEOUT_S = 15.0
RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0)

__all__ = ["SANDBOX_BASE_URL", "PayPalToolError", "RestToolExecutor"]

# tool -> (path template, send `Prefer: return=representation`)
_ROUTES: Mapping[str, tuple[str, bool]] = {
    "create_product": ("/v1/catalogs/products", True),
    "create_subscription_plan": ("/v1/billing/plans", True),
    "create_invoice": ("/v2/invoicing/invoices", False),
    "send_invoice": ("/v2/invoicing/invoices/{invoice_id}/send", False),
    "send_invoice_reminder": ("/v2/invoicing/invoices/{invoice_id}/remind", False),
    "generate_invoice_qr_code": ("/v2/invoicing/invoices/{invoice_id}/generate-qr-code", False),
}


class PayPalToolError(Exception):
    """A PayPal call failed. Holds no token, header or body content."""

    def __init__(self, tool: str, status: int, debug_id: str | None) -> None:
        super().__init__(f"PayPal tool {tool} failed: status={status} debug_id={debug_id}")
        self.tool = tool
        self.status = status
        self.debug_id = debug_id


def _body(tool: str, params: BaseModel) -> dict[str, Any]:
    data = params.model_dump(exclude_none=True)
    if tool == "create_invoice":
        return {
            "detail": {
                "invoice_number": data["invoice_number"],
                "currency_code": data["currency_code"],
                "note": data["note"],
            },
            "primary_recipients": data["primary_recipients"],
            "items": data["items"],
        }
    if tool == "send_invoice":
        return {"send_to_recipient": True}
    if tool == "send_invoice_reminder":
        return {"note": data["note"]} if "note" in data else {}
    if tool == "generate_invoice_qr_code":
        return {"width": data["width"], "height": data["height"]}
    return data  # create_product, create_subscription_plan: toolkit body as-is


def _parse(tool: str, response: httpx.Response) -> dict[str, Any]:
    text = response.text
    if tool == "generate_invoice_qr_code":
        value = text.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        return {"qr_code_base64": value}
    if tool in ("send_invoice", "send_invoice_reminder"):
        return {}
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise PayPalToolError(tool, response.status_code, response.headers.get("PayPal-Debug-Id"))
    paypal_id = payload.get("id")
    if isinstance(paypal_id, str) and paypal_id:
        return {"id": paypal_id}
    href = payload.get("href")
    if tool == "create_invoice" and isinstance(href, str) and href:
        return {"id": href.rstrip("/").rsplit("/", 1)[-1]}
    raise PayPalToolError(tool, response.status_code, response.headers.get("PayPal-Debug-Id"))


class RestToolExecutor:
    """`ToolExecutor` that calls the PayPal sandbox over REST."""

    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        base_url: str = SANDBOX_BASE_URL,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_S,
    ) -> None:
        if base_url.rstrip("/") != SANDBOX_BASE_URL:
            raise ValueError("RestToolExecutor only talks to the PayPal sandbox")
        self._token_provider = token_provider
        self._base_url = SANDBOX_BASE_URL
        self._client = client
        self._sleep = sleep
        self._retry_delays = retry_delays

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
        return self._client

    async def call(self, tool: str, params: BaseModel, *, request_id: str) -> dict[str, Any]:
        route = _ROUTES.get(tool)
        if route is None:
            raise ValueError(f"unsupported tool: {tool}")
        template, prefer = route
        invoice_id = getattr(params, "invoice_id", "")
        url = self._base_url + template.format(invoice_id=invoice_id)
        body = _body(tool, params)

        attempt = 0
        while True:
            token = await self._token_provider()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": request_id,
            }
            if prefer:
                headers["Prefer"] = "return=representation"
            try:
                response = await self._http().post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise PayPalToolError(tool, 0, None) from exc
            status = response.status_code
            if status < 400:
                return _parse(tool, response)
            retryable = status == 429 or status >= 500
            if retryable and attempt < len(self._retry_delays):
                await self._sleep(self._retry_delays[attempt])
                attempt += 1
                continue
            raise PayPalToolError(tool, status, response.headers.get("PayPal-Debug-Id"))
