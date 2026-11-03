"""PayPal webhook receiver. WP-1.2 fills the verified implementation; refusing
events now is deliberate — an HTTP 503 envelope means "not accepting money
truth", never a fake accept."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from ulid import ULID

router = APIRouter(tags=["webhooks"])


@router.post("/api/v1/webhooks/paypal")
async def paypal_webhook(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "WEBHOOK_NOT_CONFIGURED",
                "message": "Webhook verification is not wired yet (WP-1.2).",
                "details": {},
                "request_id": request.headers.get("X-Request-ID") or f"req_{ULID()}",
            }
        },
    )
