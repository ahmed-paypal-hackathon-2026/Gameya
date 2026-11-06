"""Live PayPal sandbox smoke test for RestToolExecutor.

Skipped unless PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and
PAYPAL_SANDBOX_PAYER_EMAIL are set. Deselected by default (`-m sandbox`).
Creates real sandbox objects with unique names; prints ids, never credentials.
"""

import base64
import os
import uuid

import httpx
import pytest
from app.ai.paypal_rest import SANDBOX_BASE_URL, RestToolExecutor
from app.ai.toolkit_adapter import (
    CreateInvoiceParameters,
    CreateProductParameters,
    CreateSubscriptionPlanParameters,
    GenerateInvoiceQrCodeParameters,
    SendInvoiceParameters,
    SendInvoiceReminderParameters,
    request_id,
)

_REQUIRED = ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_PAYER_EMAIL")

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(
        not all(os.environ.get(k) for k in _REQUIRED),
        reason="needs " + ", ".join(_REQUIRED),
    ),
]


async def _client_credentials_token() -> str:
    """Test-only token helper (production code injects Person 1's auth manager)."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            f"{SANDBOX_BASE_URL}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
        )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    assert isinstance(token, str)
    return token


async def test_live_product_plan_invoice_qr() -> None:
    suffix = uuid.uuid4().hex[-6:].upper()
    key = f"live-{uuid.uuid4().hex}"
    executor = RestToolExecutor(_client_credentials_token)
    try:
        product = await executor.call(
            "create_product",
            CreateProductParameters(
                name=f"Gameya live {suffix}", description="Gam'eya sandbox smoke test"
            ),
            request_id=request_id(key, "create_product"),
        )
        product_id = product["id"]
        assert product_id

        plan = await executor.call(
            "create_subscription_plan",
            CreateSubscriptionPlanParameters.model_validate(
                {
                    "product_id": product_id,
                    "name": f"Gameya live plan {suffix}",
                    "billing_cycles": [
                        {
                            "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                            "tenure_type": "REGULAR",
                            "sequence": 1,
                            "total_cycles": 3,
                            "pricing_scheme": {
                                "fixed_price": {"currency_code": "USD", "value": "1.00"}
                            },
                        }
                    ],
                    "payment_preferences": {
                        "auto_bill_outstanding": True,
                        "payment_failure_threshold": 1,
                        "setup_fee": {"currency_code": "USD", "value": "0.03"},
                        "setup_fee_failure_action": "CANCEL",
                    },
                }
            ),
            request_id=request_id(key, "create_subscription_plan"),
        )
        plan_id = plan["id"]
        assert plan_id

        # The stored plan must carry the setup fee and failure policy.
        token = await _client_credentials_token()
        async with httpx.AsyncClient(timeout=15.0) as http:
            got = await http.get(
                f"{SANDBOX_BASE_URL}/v1/billing/plans/{plan_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert got.status_code == 200
        prefs = got.json()["payment_preferences"]
        assert prefs["setup_fee"]["value"] == "0.03"
        assert prefs["payment_failure_threshold"] == 1
        assert prefs["auto_bill_outstanding"] is True
        print("stored plan payment_preferences:", prefs)

        invoice_number = f"GM-LIVE{suffix}-R1"
        invoice = await executor.call(
            "create_invoice",
            CreateInvoiceParameters.model_validate(
                {
                    "currency_code": "USD",
                    "invoice_number": invoice_number,
                    "note": "Gam'eya live smoke test: friendly contribution reminder.",
                    "primary_recipients": [
                        {
                            "billing_info": {
                                "email_address": os.environ["PAYPAL_SANDBOX_PAYER_EMAIL"]
                            }
                        }
                    ],
                    "items": [
                        {
                            "name": "Circle contribution",
                            "quantity": "1",
                            "unit_amount": {"currency_code": "USD", "value": "1.00"},
                        }
                    ],
                }
            ),
            request_id=request_id(key, "create_invoice"),
        )
        invoice_id = invoice["id"]
        assert invoice_id

        await executor.call(
            "send_invoice",
            SendInvoiceParameters(invoice_id=invoice_id),
            request_id=request_id(key, "send_invoice"),
        )
        reminder = await executor.call(
            "send_invoice_reminder",
            SendInvoiceReminderParameters(invoice_id=invoice_id, note="Friendly reminder"),
            request_id=request_id(key, "send_invoice_reminder"),
        )
        assert reminder == {}
        qr = await executor.call(
            "generate_invoice_qr_code",
            GenerateInvoiceQrCodeParameters(invoice_id=invoice_id),
            request_id=request_id(key, "generate_invoice_qr_code"),
        )
        raw_qr = qr["qr_code_base64"]
        print(f"qr_len={len(raw_qr)} qr_head={raw_qr[:12]!r}")
        assert base64.b64decode(raw_qr, validate=True).startswith(b"\x89PNG")

        print(f"product_id={product_id}")
        print(f"plan_id={plan_id}")
        print(f"invoice_id={invoice_id}")
        print(f"invoice_number={invoice_number}")
    finally:
        await executor.aclose()
