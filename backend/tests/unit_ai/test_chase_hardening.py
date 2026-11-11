"""Smart Chase hardening from the project review (I1-I3): money text, names, cap re-check."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.ai.guardrails import money_text_problem, safe_display_text
from app.ai.inputs import CycleDue, MemberDue
from app.ai.smart_chase import execute_chase, plan_chase
from app.schemas.contracts import ChaseAction, CircleId, MemberId, ReliabilityScore
from pydantic import BaseModel

from .fakes import FakeExecutor, FakeJsonModel

DUE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
LATE = DUE + timedelta(days=1)


class ChaseText(BaseModel):
    text: str


def member(**over: Any) -> MemberDue:
    data: dict[str, Any] = {
        "member_id": MemberId("mem_omar"),
        "name": "Omar",
        "email": "omar@example.com",
        "locale": "en",
        "slot_index": 2,
        "has_received_pot": False,
        "contribution_id": "con_01JC4Q8ZK3",
        "unpaid_amount": "100.00",
    }
    data.update(over)
    return MemberDue.model_validate(data)


def cycle(**over: Any) -> CycleDue:
    # cycle_index is 0-based; this is the third cycle ("cycle 3" for members).
    data: dict[str, Any] = {
        "cycle_index": 2,
        "circle_id": CircleId("cir_test"),
        "circle_name": "Family Circle",
        "member_count": 6,
        "due_at": DUE,
        "contribution_amount": "100.00",
        "reserve_balance": "0.00",
    }
    data.update(over)
    return CycleDue.model_validate(data)


def score() -> ReliabilityScore:
    return ReliabilityScore(member_id=MemberId("mem_omar"), p_late=0.2, top_factors=[])


async def _invoice_plan(text: str | None = None, **over: Any):
    models = [FakeJsonModel(ChaseText(text=text), name="gemini-test")] if text else []
    return await plan_chase(member(**over), cycle(), score(), now=LATE, json_models=models)


# ---------- I1: every number in model text must mean what the engine says ----------
async def test_wrong_dollar_amount_in_model_text_is_rejected() -> None:
    plan = await _invoice_plan("Hi Omar, only $11 is due.")
    assert plan.message_source == "template"
    assert plan.invoice_note is not None
    assert "100.00" in plan.invoice_note and "$11" not in plan.invoice_note


async def test_date_parts_cannot_pose_as_amounts() -> None:
    plan = await _invoice_plan("Hi Omar, pay $3 now, due 2026-11-10.")
    assert plan.message_source == "template"


async def test_correct_model_text_is_kept() -> None:
    text = "Hi Omar, $100.00 is due on 2026-11-10 for cycle 3."
    plan = await _invoice_plan(text)
    assert plan.message_source == "gemini"
    assert plan.invoice_note == text


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Your $100.00 for cycle 3 is due 2026-11-10.", True),
        ("Only $11 is due.", False),
        ("Pay $3 now, due 2026-11-10.", False),
        ("Pay 10 dollars.", False),
        ("Your 100 is due on 2026-11-10.", True),
        ("قسط ١٠٠٫٠٠ دولار للدورة ٣ ميعاده 2026-11-10", True),
    ],
)
def test_money_text_problem(text: str, ok: bool) -> None:
    problem = money_text_problem(text, amount=Decimal("100.00"), due=DUE.date(), cycle_number=3)
    assert (problem is None) is ok, problem


# ---------- I2: links in names never reach a PayPal invoice ----------
def test_safe_display_text_replaces_links_and_contacts() -> None:
    assert safe_display_text("Omar", "there") == "Omar"
    assert safe_display_text("Omar http://evil.example", "there") == "there"
    assert safe_display_text("help@evil.example", "there") == "there"
    assert safe_display_text("Family www.paypa1-login.com", "your circle") == "your circle"


async def test_link_in_member_name_never_reaches_the_invoice_note() -> None:
    plan = await _invoice_plan(name="Omar - pay at http://evil.example/pp or help@evil.example")
    note = plan.invoice_note or ""
    assert not any(bad in note for bad in ("http", "www", "@", "evil"))
    assert note.startswith("Hi there")


async def test_link_in_circle_name_never_reaches_the_invoice_note() -> None:
    plan = await plan_chase(
        member(), cycle(circle_name="Family www.paypa1-login.com"), score(), now=LATE,
        json_models=[],
    )  # fmt: skip
    assert "www." not in (plan.invoice_note or "")
    assert "paypa1" not in (plan.invoice_note or "")


# ---------- I3: the cap is re-checked right before PayPal is called ----------
async def test_execute_refuses_an_amount_above_the_contribution() -> None:
    plan = await _invoice_plan()
    assert plan.action is ChaseAction.INVOICE
    tampered = plan.model_copy(update={"invoice_amount": Decimal("5000.00")})
    executor = FakeExecutor()
    with pytest.raises(ValueError, match="exceeds contribution"):
        await execute_chase(tampered, executor=executor, contribution_amount=Decimal("100.00"))
    assert executor.calls == []


async def test_execute_within_the_cap_still_invoices() -> None:
    plan = await _invoice_plan()
    executor = FakeExecutor()
    await execute_chase(plan, executor=executor, contribution_amount=Decimal("100.00"))
    assert executor.calls[0][0] == "create_invoice"


# ---------- members see 1-based cycle numbers ----------
async def test_member_facing_cycle_number_is_one_based() -> None:
    plan = await _invoice_plan()
    assert "cycle 3" in (plan.invoice_note or "")
    executor = FakeExecutor()
    await execute_chase(plan, executor=executor, contribution_amount=Decimal("100.00"))
    create = executor.calls[0][1]
    assert create.items[0].name == "Gam'eya contribution, cycle 3"  # type: ignore[attr-defined]
