"""RestToolExecutor against a mocked sandbox (respx). No network."""

import json

import httpx
import pytest
import respx
from app.ai.paypal_rest import PayPalToolError, RestToolExecutor
from app.ai.toolkit_adapter import (
    CreateInvoiceParameters,
    CreateProductParameters,
    CreateSubscriptionPlanParameters,
    GenerateInvoiceQrCodeParameters,
    SendInvoiceParameters,
    SendInvoiceReminderParameters,
)

BASE = "https://api-m.sandbox.paypal.com"
RID = "11111111-1111-5111-8111-111111111111"


async def _token() -> str:
    return "tok-secret-123"


class _Executor(RestToolExecutor):
    """Executor whose backoff sleeps are recorded instead of awaited."""

    def __init__(self) -> None:
        self.delays: list[float] = []

        async def no_sleep(seconds: float) -> None:
            self.delays.append(seconds)

        super().__init__(_token, sleep=no_sleep)


def _plan(*, fee: bool) -> CreateSubscriptionPlanParameters:
    prefs: dict[str, object] = {"auto_bill_outstanding": True, "payment_failure_threshold": 1}
    if fee:
        prefs["setup_fee"] = {"currency_code": "USD", "value": "18.00"}
        prefs["setup_fee_failure_action"] = "CANCEL"
    return CreateSubscriptionPlanParameters.model_validate(
        {
            "product_id": "PROD-1",
            "name": "Circle plan",
            "billing_cycles": [
                {
                    "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 6,
                    "pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": "100.00"}},
                }
            ],
            "payment_preferences": prefs,
        }
    )


def _invoice(note: str = "Please pay your share") -> CreateInvoiceParameters:
    return CreateInvoiceParameters.model_validate(
        {
            "currency_code": "USD",
            "invoice_number": "GM-ABC1234567-R1",
            "note": note,
            "primary_recipients": [{"billing_info": {"email_address": "p@example.com"}}],
            "items": [
                {
                    "name": "Contribution",
                    "quantity": "1",
                    "unit_amount": {"currency_code": "USD", "value": "100.00"},
                }
            ],
        }
    )


def test_rejects_non_sandbox_base_url() -> None:
    with pytest.raises(ValueError):
        RestToolExecutor(_token, base_url="https://api-m.paypal.com")


@respx.mock
async def test_create_plan_body_shape_with_fee() -> None:
    route = respx.post(f"{BASE}/v1/billing/plans").respond(201, json={"id": "P-1"})
    out = await _Executor().call("create_subscription_plan", _plan(fee=True), request_id=RID)
    assert out == {"id": "P-1"}
    req = route.calls[0].request
    body = json.loads(req.content)
    assert body["payment_preferences"] == {
        "auto_bill_outstanding": True,
        "payment_failure_threshold": 1,
        "setup_fee": {"currency_code": "USD", "value": "18.00"},
        "setup_fee_failure_action": "CANCEL",
    }
    assert body["product_id"] == "PROD-1"
    assert body["name"] == "Circle plan"
    assert body["billing_cycles"] == [
        {
            "frequency": {"interval_unit": "MONTH", "interval_count": 1},
            "tenure_type": "REGULAR",
            "sequence": 1,
            "total_cycles": 6,
            "pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": "100.00"}},
        }
    ]
    assert req.headers["PayPal-Request-Id"] == RID
    assert req.headers["Authorization"] == "Bearer tok-secret-123"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["Prefer"] == "return=representation"


@respx.mock
async def test_create_plan_body_omits_fee_at_zero_reserve() -> None:
    route = respx.post(f"{BASE}/v1/billing/plans").respond(201, json={"id": "P-2"})
    await _Executor().call("create_subscription_plan", _plan(fee=False), request_id=RID)
    prefs = json.loads(route.calls[0].request.content)["payment_preferences"]
    assert prefs == {"auto_bill_outstanding": True, "payment_failure_threshold": 1}


@respx.mock
async def test_create_product() -> None:
    route = respx.post(f"{BASE}/v1/catalogs/products").respond(201, json={"id": "PROD-9"})
    out = await _Executor().call(
        "create_product", CreateProductParameters(name="Circle"), request_id=RID
    )
    assert out == {"id": "PROD-9"}
    assert json.loads(route.calls[0].request.content) == {"name": "Circle", "type": "SERVICE"}


@respx.mock
async def test_create_invoice_note_in_detail() -> None:
    route = respx.post(f"{BASE}/v2/invoicing/invoices").respond(201, json={"id": "INV2-1"})
    out = await _Executor().call("create_invoice", _invoice("Hello there"), request_id=RID)
    assert out == {"id": "INV2-1"}
    body = json.loads(route.calls[0].request.content)
    assert body["detail"] == {
        "invoice_number": "GM-ABC1234567-R1",
        "currency_code": "USD",
        "note": "Hello there",
    }
    assert body["primary_recipients"][0]["billing_info"]["email_address"] == "p@example.com"
    assert body["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "100.00"}
    assert "note" not in body


@respx.mock
async def test_invoice_id_from_href() -> None:
    respx.post(f"{BASE}/v2/invoicing/invoices").respond(
        201,
        json={"rel": "self", "href": f"{BASE}/v2/invoicing/invoices/INV2-HREF", "method": "GET"},
    )
    out = await _Executor().call("create_invoice", _invoice(), request_id=RID)
    assert out == {"id": "INV2-HREF"}


@respx.mock
async def test_send_remind_and_qr() -> None:
    send = respx.post(f"{BASE}/v2/invoicing/invoices/INV-1/send").respond(202, json={})
    remind = respx.post(f"{BASE}/v2/invoicing/invoices/INV-1/remind").respond(204)
    qr = respx.post(f"{BASE}/v2/invoicing/invoices/INV-1/generate-qr-code").respond(
        200, content=b"iVBORw0KGgo="
    )
    ex = _Executor()
    sent = await ex.call("send_invoice", SendInvoiceParameters(invoice_id="INV-1"), request_id=RID)
    assert sent == {}
    assert json.loads(send.calls[0].request.content) == {"send_to_recipient": True}
    reminded = await ex.call(
        "send_invoice_reminder",
        SendInvoiceReminderParameters(invoice_id="INV-1", note="Nudge"),
        request_id=RID,
    )
    assert reminded == {}
    assert json.loads(remind.calls[0].request.content) == {"note": "Nudge"}
    out = await ex.call(
        "generate_invoice_qr_code",
        GenerateInvoiceQrCodeParameters(invoice_id="INV-1"),
        request_id=RID,
    )
    assert out == {"qr_code_base64": "iVBORw0KGgo="}
    assert json.loads(qr.calls[0].request.content) == {"width": 300, "height": 300}


@respx.mock
async def test_qr_returns_base64_from_json_string() -> None:
    respx.post(f"{BASE}/v2/invoicing/invoices/INV-1/generate-qr-code").respond(
        200, content=b'"AAAA"', headers={"Content-Type": "application/json"}
    )
    out = await _Executor().call(
        "generate_invoice_qr_code",
        GenerateInvoiceQrCodeParameters(invoice_id="INV-1"),
        request_id=RID,
    )
    assert out == {"qr_code_base64": "AAAA"}


@respx.mock
async def test_request_id_header_sent_and_reused_on_retry() -> None:
    route = respx.post(f"{BASE}/v1/catalogs/products").mock(
        side_effect=[httpx.Response(503), httpx.Response(201, json={"id": "PROD-R"})]
    )
    ex = _Executor()
    out = await ex.call("create_product", CreateProductParameters(name="C"), request_id=RID)
    assert out == {"id": "PROD-R"}
    assert route.call_count == 2
    assert [c.request.headers["PayPal-Request-Id"] for c in route.calls] == [RID, RID]
    assert ex.delays == [0.5]


@respx.mock
async def test_retries_exhausted_raises_with_debug_id() -> None:
    route = respx.post(f"{BASE}/v1/catalogs/products").respond(
        429, headers={"PayPal-Debug-Id": "dbg-1"}
    )
    ex = _Executor()
    with pytest.raises(PayPalToolError) as ei:
        await ex.call("create_product", CreateProductParameters(name="C"), request_id=RID)
    assert route.call_count == 3
    assert ex.delays == [0.5, 1.0]
    assert (ei.value.tool, ei.value.status, ei.value.debug_id) == ("create_product", 429, "dbg-1")


@respx.mock
async def test_no_retry_on_400_and_error_hides_secrets() -> None:
    route = respx.post(f"{BASE}/v1/billing/plans").respond(
        400, headers={"PayPal-Debug-Id": "dbg-400"}, json={"name": "INVALID_REQUEST"}
    )
    with pytest.raises(PayPalToolError) as ei:
        await _Executor().call("create_subscription_plan", _plan(fee=False), request_id=RID)
    assert route.call_count == 1
    assert ei.value.status == 400
    assert ei.value.debug_id == "dbg-400"
    text = f"{ei.value!s} {ei.value!r}"
    assert "tok-secret-123" not in text
    assert "Circle plan" not in text


@respx.mock
async def test_unknown_tool_rejected() -> None:
    with pytest.raises(ValueError):
        await _Executor().call("create_payout", CreateProductParameters(name="x"), request_id=RID)


@respx.mock
async def test_token_provider_failure_becomes_tool_error() -> None:
    async def boom() -> str:
        raise httpx.ConnectTimeout("secret-client-id timed out")

    route = respx.post(f"{BASE}/v1/catalogs/products")
    ex = RestToolExecutor(boom)
    with pytest.raises(PayPalToolError) as ei:
        await ex.call("create_product", CreateProductParameters(name="C"), request_id=RID)
    assert ei.value.status == 0
    assert ei.value.__cause__ is None
    assert "secret-client-id" not in f"{ei.value!s} {ei.value!r}"
    assert route.call_count == 0


@respx.mock
async def test_invoice_id_is_path_quoted() -> None:
    route = respx.post(
        url__regex=r"https://api-m\.sandbox\.paypal\.com/v2/invoicing/invoices/.*"
    ).respond(202, json={})
    await _Executor().call(
        "send_invoice", SendInvoiceParameters(invoice_id="../x?y"), request_id=RID
    )
    assert route.calls[0].request.url.raw_path == b"/v2/invoicing/invoices/..%2Fx%3Fy/send"


@respx.mock
async def test_transport_error_is_status_zero_single_attempt() -> None:
    route = respx.post(f"{BASE}/v1/catalogs/products").mock(side_effect=httpx.ConnectError("boom"))
    ex = _Executor()
    with pytest.raises(PayPalToolError) as ei:
        await ex.call("create_product", CreateProductParameters(name="C"), request_id=RID)
    assert ei.value.status == 0
    assert route.call_count == 1
    assert ex.delays == []
