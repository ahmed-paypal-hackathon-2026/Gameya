"""Task 1: additive ChasePlan/ChaseAction contract, engine input types and prefixed ids."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.ai.ids import new_id
from app.ai.inputs import CycleDue, MemberDue
from app.schemas.contracts import ChaseAction, ChasePlan
from pydantic import ValidationError


def _member(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "member_id": "mem_1",
        "name": "Salma",
        "email": "salma@example.com",
        "locale": "en",
        "slot_index": 2,
        "has_received_pot": False,
        "contribution_id": "con_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "unpaid_amount": Decimal("100.00"),
        "open_invoice_id": None,
        "invoice_attempts": 0,
        "reminders_sent": 0,
    }
    base.update(over)
    return base


def _cycle(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cycle_index": 1,
        "circle_id": "cir_1",
        "circle_name": "Friends",
        "member_count": 6,
        "due_at": datetime(2026, 11, 1, tzinfo=UTC),
        "contribution_amount": Decimal("100.00"),
        "currency": "USD",
        "reserve_balance": Decimal("18.00"),
    }
    base.update(over)
    return base


def test_reminder_requires_invoice_id_and_note() -> None:
    common: dict[str, Any] = {
        "member_id": "mem_1",
        "cycle_index": 1,
        "action": ChaseAction.REMINDER,
    }
    with pytest.raises(ValidationError):
        ChasePlan(**common, invoice_note="Please pay")
    with pytest.raises(ValidationError):
        ChasePlan(**common, invoice_id="INV2-ABC")
    plan = ChasePlan(**common, invoice_id="INV2-ABC", invoice_note="Please pay")
    assert plan.action is ChaseAction.REMINDER
    assert plan.locale == "en"
    assert plan.message_source is None


def test_new_optional_plan_fields_accepted() -> None:
    plan = ChasePlan(
        member_id="mem_1",
        cycle_index=1,
        action=ChaseAction.INVOICE,
        invoice_amount=Decimal("100"),
        invoice_note="Pay up",
        plan_id="chp_X",
        contribution_id="con_X",
        recipient_email="a@b.com",
        locale="ar-EG",
        invoice_number="GM-X-R1",
        message_source="template",
    )
    assert plan.locale == "ar-EG"
    with pytest.raises(ValidationError):
        ChasePlan(member_id="mem_1", cycle_index=1, action=ChaseAction.NOOP, message_source="llm")  # type: ignore[arg-type]


def test_legacy_plans_and_fixtures_still_validate() -> None:
    ChasePlan(member_id="mem_1", cycle_index=0, action=ChaseAction.NOOP)
    ChasePlan(member_id="mem_1", cycle_index=0, action=ChaseAction.NUDGE, nudge_text="hi")
    ChasePlan(
        member_id="mem_1",
        cycle_index=0,
        action=ChaseAction.INVOICE,
        invoice_amount=Decimal("5"),
        invoice_note="n",
    )


def test_member_due_and_cycle_due_valid_and_frozen() -> None:
    m = MemberDue(**_member())
    c = CycleDue(**_cycle())
    assert m.unpaid_amount == Decimal("100.00")
    assert c.reserve_balance == Decimal("18.00")
    with pytest.raises(ValidationError):
        m.name = "x"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["unpaid_amount"])
def test_member_due_rejects_float_money(field: str) -> None:
    with pytest.raises(ValidationError):
        MemberDue(**_member(**{field: 100.0}))


@pytest.mark.parametrize("field", ["contribution_amount", "reserve_balance"])
def test_cycle_due_rejects_float_money(field: str) -> None:
    with pytest.raises(ValidationError):
        CycleDue(**_cycle(**{field: 10.5}))


def test_due_types_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MemberDue(**_member(surprise=1))


@pytest.mark.parametrize("prefix", ["agd", "chp", "med"])
def test_new_id_prefixes(prefix: Any) -> None:
    a, b = new_id(prefix), new_id(prefix)
    assert a.startswith(f"{prefix}_")
    assert len(a) <= 30
    assert a != b
