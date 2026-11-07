"""Toolkit-shaped tool parameters, declarations, plan validation and the guarded executor.

The parameter models mirror the field names of `paypal-agent-toolkit` 1.11.0
(the same approach as Project 1's `paypal_tools.py`). The package itself is not
a dependency: only a conformance test imports it, when it is installed.

This is the only door through which AI code reaches PayPal. A model may propose
tool calls, but `validate_tool_plan` accepts them only when they equal what the
engine built, and `GuardedExecutor` enforces the per-agent allow-list. The
Agent Toolkit has no Payouts tool and none is declared here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_DNS, uuid5

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.contracts import AgentName, ToolName
from ..schemas.contracts import ToolCall as AuditToolCall
from .guardrails import check_tool_allowed
from .llm import ToolCall

AGENT_TOOLKIT_VERSION = "1.11.0"

__all__ = [
    "AGENT_TOOLKIT_VERSION",
    "TOOL_PARAMS",
    "BillingCycle",
    "CreateInvoiceParameters",
    "CreateProductParameters",
    "CreateSubscriptionPlanParameters",
    "GenerateInvoiceQrCodeParameters",
    "GuardedExecutor",
    "SendInvoiceParameters",
    "SendInvoiceReminderParameters",
    "ToolExecutor",
    "binding_view",
    "request_id",
    "tool_declarations",
    "validate_tool_plan",
]

_CENT = Decimal("0.01")
_SAFE_ARG_MAX_CHARS = 64


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanMoney(_Strict):
    """Toolkit money: `value` is a decimal string (compared at 2 dp)."""

    currency_code: Literal["USD"]
    value: str


class Frequency(_Strict):
    interval_unit: Literal["DAY", "WEEK", "MONTH", "YEAR"]
    interval_count: int = Field(ge=1)


class PricingScheme(_Strict):
    fixed_price: PlanMoney


class BillingCycle(_Strict):
    frequency: Frequency
    tenure_type: Literal["REGULAR", "TRIAL"]
    sequence: int = Field(ge=1)
    total_cycles: int = Field(ge=0)
    pricing_scheme: PricingScheme


class PaymentPreferences(_Strict):
    auto_bill_outstanding: bool
    payment_failure_threshold: int = Field(ge=1)
    setup_fee: PlanMoney | None = None
    setup_fee_failure_action: Literal["CONTINUE", "CANCEL"] | None = None


class CreateProductParameters(_Strict):
    name: str
    type: Literal["SERVICE"] = "SERVICE"
    description: str | None = None


class CreateSubscriptionPlanParameters(_Strict):
    product_id: str
    name: str
    description: str | None = None
    billing_cycles: list[BillingCycle]
    payment_preferences: PaymentPreferences


class BillingInfo(_Strict):
    email_address: str


class PrimaryRecipient(_Strict):
    billing_info: BillingInfo


class Item(_Strict):
    name: str
    quantity: str
    unit_amount: PlanMoney


class CreateInvoiceParameters(_Strict):
    currency_code: Literal["USD"]
    invoice_number: str
    note: str = Field(max_length=4000)
    primary_recipients: list[PrimaryRecipient]
    items: list[Item]


class SendInvoiceParameters(_Strict):
    invoice_id: str


class SendInvoiceReminderParameters(_Strict):
    invoice_id: str
    note: str | None = None


class GenerateInvoiceQrCodeParameters(_Strict):
    invoice_id: str
    width: int = 300
    height: int = 300


TOOL_PARAMS: Mapping[str, type[BaseModel]] = {
    "create_product": CreateProductParameters,
    "create_subscription_plan": CreateSubscriptionPlanParameters,
    "create_invoice": CreateInvoiceParameters,
    "send_invoice": SendInvoiceParameters,
    "send_invoice_reminder": SendInvoiceReminderParameters,
    "generate_invoice_qr_code": GenerateInvoiceQrCodeParameters,
}

_DESCRIPTIONS: Mapping[str, str] = {
    "create_product": "Create a PayPal catalog product for a savings circle.",
    "create_subscription_plan": "Create a monthly or weekly subscription plan for a product.",
    "create_invoice": "Create a draft PayPal invoice for one member's contribution.",
    "send_invoice": "Send an existing draft invoice to its recipient.",
    "send_invoice_reminder": "Send a reminder for an already-sent invoice.",
    "generate_invoice_qr_code": "Generate a payment QR code for an invoice.",
}


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Return `schema` with every `$ref` replaced by its `$defs` entry."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                return resolve(defs[ref.rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def tool_declarations(names: Sequence[str]) -> list[dict[str, Any]]:
    """Self-contained function declarations (no `$ref`) for the named tools."""
    return [
        {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": _inline_refs(TOOL_PARAMS[name].model_json_schema()),
        }
        for name in names
    ]


def _normalise_money(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, val in node.items():
            if key == "value":
                try:
                    dec = Decimal(str(val))
                except InvalidOperation as exc:
                    raise ValueError("malformed money value") from exc
                if not dec.is_finite():
                    raise ValueError("malformed money value")
                out[key] = str(dec.quantize(_CENT, rounding=ROUND_HALF_UP))
            else:
                out[key] = _normalise_money(val)
        return out
    if isinstance(node, list):
        return [_normalise_money(item) for item in node]
    return node


def binding_view(params: BaseModel) -> dict[str, Any]:
    """JSON dump with every nested `value` money field quantised to 0.01.

    Raises `ValueError` on a non-numeric money value.
    """
    view: dict[str, Any] = _normalise_money(params.model_dump(mode="json"))
    return view


def validate_tool_plan(
    calls: Sequence[ToolCall],
    canonical: Sequence[tuple[str, BaseModel | None]],
) -> bool:
    """True only if `calls` is a non-empty in-order prefix of the engine's plan.

    A canonical `None` means the engine fills the arguments from an earlier
    step (such as `product_id`), so only the name is checked. Never raises.
    """
    try:
        if not calls or len(calls) > len(canonical):
            return False
        for call, (name, expected) in zip(calls, canonical, strict=False):
            if call.name != name or name not in TOOL_PARAMS:
                return False
            if expected is None:
                continue
            proposed = TOOL_PARAMS[name].model_validate(call.args)
            if binding_view(proposed) != binding_view(expected):
                return False
        return True
    except Exception:  # noqa: BLE001 - any malformed proposal is simply rejected
        return False


class ToolExecutor(Protocol):
    """Runs one toolkit-shaped call and returns the parsed PayPal JSON."""

    async def call(self, tool: str, params: BaseModel, *, request_id: str) -> dict[str, Any]: ...


def request_id(key: str, tool: str) -> str:
    """Stable `PayPal-Request-Id` for a (key, tool) pair."""
    return str(uuid5(NAMESPACE_DNS, f"{key}:{tool}"))


_EMAIL_LIKE = re.compile(r"@|email", re.IGNORECASE)


def _safe_arguments(params: BaseModel) -> dict[str, str | int | Decimal | None]:
    """Short top-level primitives only; never emails or nested bodies."""
    safe: dict[str, str | int | Decimal | None] = {}
    for key, val in params.model_dump(mode="json").items():
        if _EMAIL_LIKE.search(key):
            continue
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            safe[key] = val
        elif isinstance(val, str) and len(val) <= _SAFE_ARG_MAX_CHARS and "@" not in val:
            safe[key] = val
    return safe


class GuardedExecutor:
    """Enforces the agent's tool allow-list and keeps an audit trail."""

    def __init__(self, agent: AgentName, inner: ToolExecutor) -> None:
        self._agent = agent
        self._inner = inner
        self.records: list[AuditToolCall] = []

    async def call(
        self, tool: ToolName | str, params: BaseModel, *, request_id: str
    ) -> dict[str, Any]:
        check_tool_allowed(self._agent, tool)
        name = ToolName(tool.value if isinstance(tool, ToolName) else tool)
        arguments = _safe_arguments(params)
        try:
            result = await self._inner.call(name.value, params, request_id=request_id)
        except Exception as exc:
            self.records.append(
                AuditToolCall(
                    name=name,
                    arguments=arguments,
                    result_summary=f"failed: {type(exc).__name__}",
                )
            )
            raise
        paypal_id = result.get("id")
        self.records.append(
            AuditToolCall(
                name=name,
                arguments=arguments,
                result_summary=paypal_id if isinstance(paypal_id, str) else None,
            )
        )
        return result
