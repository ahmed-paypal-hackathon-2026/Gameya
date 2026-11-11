"""The PayPal tool executor that routes hand to the AI agents (sandbox only).

The agents never see credentials: they get a `ToolExecutor`, and every call it makes
goes through the cached OAuth token in `auth.py`. Tests override `get_tool_executor`
with a fake, so no test talks to PayPal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import HTTPException

from ...ai.paypal_rest import SANDBOX_BASE_URL, RestToolExecutor
from ...ai.toolkit_adapter import ToolExecutor
from .auth import get_paypal_auth


async def get_tool_executor() -> AsyncIterator[ToolExecutor]:
    """FastAPI dependency: a sandbox REST executor, or 503 when PayPal is not configured."""
    try:
        auth = get_paypal_auth()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="PayPal sandbox credentials are not configured"
        ) from exc
    executor = RestToolExecutor(token_provider=auth.token, base_url=SANDBOX_BASE_URL)
    try:
        yield executor
    finally:
        await executor.aclose()
