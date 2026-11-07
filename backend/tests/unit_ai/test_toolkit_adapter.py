from __future__ import annotations

import json
from typing import Any

import pytest
from app.ai import llm, toolkit_adapter
from app.ai.guardrails import ALLOW_LISTS, ToolNotAllowed
from app.ai.llm import ToolCall as ProposedCall
from app.ai.toolkit_adapter import (
    CreateInvoiceParameters,
    CreateProductParameters,
    CreateSubscriptionPlanParameters,
    GuardedExecutor,
    SendInvoiceParameters,
    binding_view,
    request_id,
    tool_declarations,
    validate_tool_plan,
)
from app.schemas.contracts import AgentName, ToolName
from pydantic import ValidationError

from .fakes import FakeExecutor, FakeJsonModel, FakeToolPlanModel

PRODUCT_ID = "PROD-2F418192GOLDEN"


def _plan_dict(
    value: str = "100.00", total_cycles: int = 6, currency: str = "USD", fee: bool = True
) -> dict[str, Any]:
    prefs: dict[str, Any] = {"auto_bill_outstanding": True, "payment_failure_threshold": 1}
    if fee:
        prefs["setup_fee"] = {"currency_code": "USD", "value": "18.00"}
        prefs["setup_fee_failure_action"] = "CANCEL"
    return {
        "product_id": PRODUCT_ID,
        "name": "Gam'eya Circle",
        "billing_cycles": [
            {
                "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": total_cycles,
                "pricing_scheme": {"fixed_price": {"currency_code": currency, "value": value}},
            }
        ],
        "payment_preferences": prefs,
    }


def _plan(**kw: Any) -> CreateSubscriptionPlanParameters:
    return CreateSubscriptionPlanParameters.model_validate(_plan_dict(**kw))


def _invoice_dict(value: str = "100.00") -> dict[str, Any]:
    return {
        "currency_code": "USD",
        "invoice_number": "GM-0123456789-R1",
        "note": "Friendly reminder",
        "primary_recipients": [{"billing_info": {"email_address": "member@example.com"}}],
        "items": [
            {
                "name": "Gam'eya contribution",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": value},
            }
        ],
    }


def _call(name: str, args: dict[str, Any]) -> ProposedCall:
    return ProposedCall(name=name, args=args)


def test_declarations_cover_allowed_tools() -> None:
    names = sorted(t.value for t in ALLOW_LISTS[AgentName.SMART_CHASE])
    decls = tool_declarations(names)
    assert len(decls) == 4
    assert {d["name"] for d in decls} == set(names)
    assert "$ref" not in json.dumps(decls)
    assert all(d["description"] and d["parameters"] for d in decls)


def test_declarations_unknown_tool_raises() -> None:
    with pytest.raises(KeyError):
        tool_declarations(["create_payout"])


def test_validate_plan_accepts_equal_amount_formats() -> None:
    canon = [("create_subscription_plan", _plan(value="100.00"))]
    assert validate_tool_plan([_call("create_subscription_plan", _plan_dict("100"))], canon)
    assert validate_tool_plan([_call("create_subscription_plan", _plan_dict("100.0"))], canon)


@pytest.mark.parametrize(
    "kw",
    [{"value": "1000.00"}, {"total_cycles": 60}, {"currency": "EUR"}],
)
def test_validate_plan_rejects_altered_amount_cycles_currency(kw: dict[str, Any]) -> None:
    canon = [("create_subscription_plan", _plan())]
    assert not validate_tool_plan([_call("create_subscription_plan", _plan_dict(**kw))], canon)


def test_validate_plan_rejects_altered_setup_fee_and_nested_value() -> None:
    canon = [("create_subscription_plan", _plan())]
    bad = _plan_dict()
    bad["payment_preferences"]["setup_fee"]["value"] = "0.00"
    assert not validate_tool_plan([_call("create_subscription_plan", bad)], canon)
    no_fee = _plan_dict(fee=False)
    assert not validate_tool_plan([_call("create_subscription_plan", no_fee)], canon)


@pytest.mark.parametrize(
    "bad",
    ["abc", "", "NaN", "Infinity", "100.004", " 100.00 ", "+100", "1_00", "100.00\n", "-5", "1e2"],
)
def test_validate_plan_malformed_money_returns_false(bad: str) -> None:
    canon = [("create_subscription_plan", _plan())]
    assert validate_tool_plan([_call("create_subscription_plan", _plan_dict(bad))], canon) is False


def test_validate_plan_rejects_wrong_order_extra_fields_unknown_tool() -> None:
    product = CreateProductParameters(name="Circle")
    canon = [("create_product", product), ("create_subscription_plan", None)]
    prod_call = _call("create_product", {"name": "Circle", "type": "SERVICE"})
    plan_call = _call("create_subscription_plan", _plan_dict())
    assert validate_tool_plan([prod_call, plan_call], canon)
    assert validate_tool_plan([prod_call], canon)  # prefix
    assert not validate_tool_plan([plan_call, prod_call], canon)
    assert not validate_tool_plan([], canon)
    assert not validate_tool_plan([prod_call, plan_call, plan_call], canon)
    extra = _call("create_product", {"name": "Circle", "type": "SERVICE", "category": "X"})
    assert not validate_tool_plan([extra], canon)
    assert not validate_tool_plan([_call("create_payout", {})], [("create_payout", None)])
    assert not validate_tool_plan([_call("create_product", {"nope": 1})], canon)


def test_validate_plan_rejects_altered_threshold_and_failure_action() -> None:
    canon = [("create_subscription_plan", _plan())]
    bad = _plan_dict()
    bad["payment_preferences"]["payment_failure_threshold"] = 3
    assert not validate_tool_plan([_call("create_subscription_plan", bad)], canon)
    bad = _plan_dict()
    bad["payment_preferences"]["setup_fee_failure_action"] = "CONTINUE"
    assert not validate_tool_plan([_call("create_subscription_plan", bad)], canon)


def test_validate_invoice_rejects_altered_invoice_number() -> None:
    inv = CreateInvoiceParameters.model_validate(_invoice_dict())
    bad = _invoice_dict()
    bad["invoice_number"] = "GM-0123456789-R2"
    assert not validate_tool_plan([_call("create_invoice", bad)], [("create_invoice", inv)])


def test_validate_plan_none_canonical_checks_name_only() -> None:
    canon = [("create_product", None)]
    assert validate_tool_plan([_call("create_product", {"anything": 1})], canon)


def test_validate_invoice_amount_and_recipient() -> None:
    inv = CreateInvoiceParameters.model_validate(_invoice_dict())
    canon = [("create_invoice", inv)]
    assert validate_tool_plan([_call("create_invoice", _invoice_dict("100"))], canon)
    assert not validate_tool_plan([_call("create_invoice", _invoice_dict("150.00"))], canon)
    other = _invoice_dict()
    other["primary_recipients"][0]["billing_info"]["email_address"] = "evil@example.com"
    assert not validate_tool_plan([_call("create_invoice", other)], canon)


def test_binding_view_normalises_nested_values() -> None:
    view = binding_view(_plan(value="100"))
    assert view["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["value"] == "100.00"
    assert view["payment_preferences"]["setup_fee"]["value"] == "18.00"
    inv = binding_view(CreateInvoiceParameters.model_validate(_invoice_dict("7.5")))
    assert inv["items"][0]["unit_amount"]["value"] == "7.50"


def test_payment_failure_threshold_must_be_at_least_one() -> None:
    bad = _plan_dict()
    bad["payment_preferences"]["payment_failure_threshold"] = 0
    with pytest.raises(ValidationError):
        CreateSubscriptionPlanParameters.model_validate(bad)


def test_canonical_dump_has_toolkit_shape() -> None:
    dump = _plan().model_dump(exclude_none=True)
    assert dump["payment_preferences"] == {
        "auto_bill_outstanding": True,
        "payment_failure_threshold": 1,
        "setup_fee": {"currency_code": "USD", "value": "18.00"},
        "setup_fee_failure_action": "CANCEL",
    }
    assert "description" not in dump


def test_toolkit_conformance() -> None:
    pytest.importorskip("paypal_agent_toolkit")
    try:
        from paypal_agent_toolkit.shared.invoices import parameters as inv_p
        from paypal_agent_toolkit.shared.subscriptions import parameters as sub_p
    except ImportError:  # pragma: no cover
        pytest.skip("toolkit parameter modules not importable")
    sub_p.CreateSubscriptionPlanParameters.model_validate(_plan().model_dump(exclude_none=True))
    sub_p.CreateProductParameters.model_validate(
        CreateProductParameters(name="Circle", description="d").model_dump(exclude_none=True)
    )
    inv_p.CreateInvoiceParameters.model_validate(
        CreateInvoiceParameters.model_validate(_invoice_dict()).model_dump(exclude_none=True)
    )
    inv_p.SendInvoiceParameters.model_validate(
        SendInvoiceParameters(invoice_id="INV2-ABCD").model_dump(exclude_none=True)
    )


async def test_guarded_executor_blocks_disallowed_tool() -> None:
    fake = FakeExecutor()
    guarded = GuardedExecutor(AgentName.CIRCLE_AGENT, fake)
    inv = CreateInvoiceParameters.model_validate(_invoice_dict())
    with pytest.raises(ToolNotAllowed):
        await guarded.call("create_invoice", inv, request_id="r1")
    assert fake.calls == []
    assert guarded.records == []


async def test_guarded_executor_rejects_mismatched_params() -> None:
    fake = FakeExecutor()
    guarded = GuardedExecutor(AgentName.SMART_CHASE, fake)
    with pytest.raises(TypeError):
        await guarded.call(
            "send_invoice_reminder", SendInvoiceParameters(invoice_id="INV2-1"), request_id="r"
        )
    assert fake.calls == []
    assert guarded.records == []


async def test_guarded_executor_blocks_agents_without_tools() -> None:
    fake = FakeExecutor()
    guarded = GuardedExecutor(AgentName.MEDIATION, fake)
    with pytest.raises(ToolNotAllowed):
        await guarded.call("create_product", CreateProductParameters(name="x"), request_id="r")
    assert fake.calls == []


async def test_guarded_executor_records_success() -> None:
    fake = FakeExecutor(results={"create_invoice": {"id": "INV2-ABC"}})
    guarded = GuardedExecutor(AgentName.SMART_CHASE, fake)
    inv = CreateInvoiceParameters.model_validate(_invoice_dict())
    out = await guarded.call(ToolName.CREATE_INVOICE, inv, request_id="r1")
    assert out == {"id": "INV2-ABC"}
    (rec,) = guarded.records
    assert rec.name is ToolName.CREATE_INVOICE
    assert rec.result_summary == "INV2-ABC"
    assert "member@example.com" not in repr(rec.arguments)
    assert fake.calls[0][2] == "r1"


async def test_guarded_executor_records_failures() -> None:
    fake = FakeExecutor(raises={"send_invoice": RuntimeError("token abc123 leaked")})
    guarded = GuardedExecutor(AgentName.SMART_CHASE, fake)
    with pytest.raises(RuntimeError):
        await guarded.call(
            "send_invoice", SendInvoiceParameters(invoice_id="INV2-1"), request_id="r"
        )
    (rec,) = guarded.records
    assert rec.result_summary == "failed: RuntimeError"
    assert "abc123" not in repr(rec)


async def test_fake_executor_idempotent_ids() -> None:
    fake = FakeExecutor()
    p = CreateProductParameters(name="x")
    a = await fake.call("create_product", p, request_id="same")
    b = await fake.call("create_product", p, request_id="same")
    c = await fake.call("create_product", p, request_id="other")
    assert a["id"] == b["id"] != c["id"]


def test_request_id_stable() -> None:
    a = request_id("plan_1", "create_invoice")
    assert a == request_id("plan_1", "create_invoice")
    assert a != request_id("plan_1", "send_invoice")
    assert a != request_id("plan_2", "create_invoice")


async def test_fake_models_satisfy_protocols() -> None:
    jm: llm.JsonModel = FakeJsonModel(result=CreateProductParameters(name="x"))
    tm: llm.ToolPlanModel = FakeToolPlanModel(calls=[_call("create_product", {"name": "x"})])
    assert (await jm.generate_json("s", "u", CreateProductParameters)).name == "x"
    planned = await tm.generate_tool_calls("s", "u", [], ["create_product"])
    assert planned[0].name == "create_product"
    with pytest.raises(llm.LLMUnavailable):
        await FakeJsonModel(error=llm.LLMUnavailable("down")).generate_json(
            "s", "u", CreateProductParameters
        )
    with pytest.raises(llm.LLMUnavailable):
        await FakeToolPlanModel(error=llm.LLMUnavailable("down")).generate_tool_calls(
            "s", "u", [], []
        )


def test_module_exports_version() -> None:
    assert toolkit_adapter.AGENT_TOOLKIT_VERSION == "1.11.0"
