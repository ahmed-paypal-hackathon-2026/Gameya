"""Smart Chase: deterministic chase rules, number-checked messages, guarded execution."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.ai.inputs import CycleDue, MemberDue
from app.ai.smart_chase import (
    HIGH_RISK_P_LATE,
    NUDGE_WINDOW,
    plan_chase,
    risk_reasons,
)
from app.schemas.contracts import (
    ChaseAction,
    CircleId,
    MemberId,
    ReliabilityFactor,
    ReliabilityScore,
)
from pydantic import BaseModel

from .fakes import FakeJsonModel

DUE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


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
    data: dict[str, Any] = {
        "cycle_index": 3,
        "circle_id": CircleId("cir_test"),
        "circle_name": "Family Circle",
        "member_count": 6,
        "due_at": DUE,
        "contribution_amount": "100.00",
        "reserve_balance": "0.00",
    }
    data.update(over)
    return CycleDue.model_validate(data)


def score(p_late: float = 0.2, factors: list[ReliabilityFactor] | None = None) -> ReliabilityScore:
    return ReliabilityScore(
        member_id=MemberId("mem_omar"), p_late=p_late, top_factors=factors or []
    )


async def test_plan_none_when_paid() -> None:
    plan = await plan_chase(
        member(unpaid_amount="0.00"),
        cycle(),
        score(0.9),
        now=DUE + timedelta(days=5),
        json_models=[],
    )
    assert plan.action is ChaseAction.NOOP
    assert plan.nudge_text is None and plan.invoice_note is None


async def test_nudge_high_risk_within_3_days() -> None:
    plan = await plan_chase(
        member(), cycle(), score(0.70), now=DUE - timedelta(days=2), json_models=[]
    )
    assert plan.action is ChaseAction.NUDGE
    assert plan.nudge_text


async def test_nudge_boundary_is_inclusive() -> None:
    assert HIGH_RISK_P_LATE == Decimal("0.50")
    assert NUDGE_WINDOW == timedelta(days=3)
    plan = await plan_chase(member(), cycle(), score(0.5), now=DUE - NUDGE_WINDOW, json_models=[])
    assert plan.action is ChaseAction.NUDGE


async def test_no_nudge_low_risk() -> None:
    plan = await plan_chase(
        member(), cycle(), score(0.20), now=DUE - timedelta(days=2), json_models=[]
    )
    assert plan.action is ChaseAction.NOOP


async def test_no_nudge_outside_window() -> None:
    plan = await plan_chase(
        member(), cycle(), score(0.9), now=DUE - timedelta(days=4), json_models=[]
    )
    assert plan.action is ChaseAction.NOOP


async def test_grace_day_none() -> None:
    for delta in (timedelta(0), timedelta(hours=23)):
        plan = await plan_chase(member(), cycle(), score(0.9), now=DUE + delta, json_models=[])
        assert plan.action is ChaseAction.NOOP


async def test_invoice_after_due_plus_one() -> None:
    plan = await plan_chase(member(), cycle(), score(), now=DUE + timedelta(days=1), json_models=[])
    assert plan.action is ChaseAction.INVOICE
    assert plan.invoice_number == "GM-01JC4Q8ZK3-R1"
    assert plan.invoice_amount == Decimal("100.00")
    assert plan.contribution_id == "con_01JC4Q8ZK3"
    assert plan.recipient_email == "omar@example.com"
    assert plan.plan_id is not None and plan.plan_id.startswith("chp_")
    assert plan.cycle_index == 3


async def test_invoice_number_uses_attempt_counter() -> None:
    plan = await plan_chase(
        member(invoice_attempts=1), cycle(), score(), now=DUE + timedelta(days=2), json_models=[]
    )
    assert plan.invoice_number == "GM-01JC4Q8ZK3-R2"


async def test_reminder_when_invoice_open() -> None:
    plan = await plan_chase(
        member(open_invoice_id="INV2-ABCD", invoice_attempts=1),
        cycle(),
        score(),
        now=DUE + timedelta(days=2),
        json_models=[],
    )
    assert plan.action is ChaseAction.REMINDER
    assert plan.invoice_id == "INV2-ABCD"
    assert plan.invoice_note
    assert plan.invoice_amount is None


async def test_invoice_amount_over_contribution_raises() -> None:
    with pytest.raises(ValueError):
        await plan_chase(
            member(unpaid_amount="150.00"),
            cycle(),
            score(),
            now=DUE + timedelta(days=2),
            json_models=[],
        )


async def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValueError):
        await plan_chase(member(), cycle(), score(), now=datetime(2026, 11, 12), json_models=[])
    with pytest.raises(ValueError):
        await plan_chase(
            member(),
            cycle(due_at=datetime(2026, 11, 10)),
            score(),
            now=DUE,
            json_models=[],
        )


def test_risk_reasons_positive_contributions_in_order() -> None:
    factors = [
        ReliabilityFactor(feature="a", contribution=0.4, label="Paid late recently"),
        ReliabilityFactor(feature="b", contribution=-0.3, label="Long history"),
        ReliabilityFactor(feature="c", contribution=0.1, label="Invoices needed"),
    ]
    assert risk_reasons(score(0.6, factors)) == ["Paid late recently", "Invoices needed"]


async def test_plan_chase_never_calls_executor() -> None:
    params = inspect.signature(plan_chase).parameters
    assert "executor" not in params
    from .fakes import FakeExecutor

    executor = FakeExecutor()
    await plan_chase(member(), cycle(), score(), now=DUE + timedelta(days=2), json_models=[])
    assert executor.calls == []


# --- messages -------------------------------------------------------------------------------

EN_INVOICE = (
    "Hi Omar, this invoice covers your unpaid Gam'eya contribution of $100.00 for "
    "cycle 3 of Family Circle."
)
EN_NUDGE = (
    "Hi Omar, your Gam'eya contribution of $100.00 for cycle 3 is due on "
    "2026-11-10. Paying on time keeps the circle on track for everyone."
)
LATE = DUE + timedelta(days=2)
LABELLED = [ReliabilityFactor(feature="late", contribution=0.4, label="Paid late recently")]


class ChaseText(BaseModel):
    text: str


async def test_model_text_used_when_numbers_ok() -> None:
    text = "Hi Omar, a gentle note about your $100.00 for cycle 3, due 2026-11-10. Thank you!"
    model = FakeJsonModel(ChaseText(text=text), name="gemini-test")
    plan = await plan_chase(member(), cycle(), score(), now=LATE, json_models=[model])
    assert plan.invoice_note == text
    assert plan.message_source == "gemini"
    assert plan.agent_decision is not None and plan.agent_decision.model == "gemini-test"
    assert len(model.prompts) == 1


async def test_message_rejects_invented_number_uses_template() -> None:
    model = FakeJsonModel(ChaseText(text="Hi Omar, please pay $150 now."))
    plan = await plan_chase(member(), cycle(), score(), now=LATE, json_models=[model])
    assert plan.message_source == "template"
    assert plan.invoice_note == EN_INVOICE


async def test_nudge_template_exact() -> None:
    plan = await plan_chase(
        member(), cycle(), score(0.8), now=DUE - timedelta(days=1), json_models=[]
    )
    assert plan.nudge_text == EN_NUDGE
    assert plan.invoice_note is None


async def test_message_over_600_chars_uses_template() -> None:
    model = FakeJsonModel(ChaseText(text="Please pay soon. " * 40))
    plan = await plan_chase(member(), cycle(), score(), now=LATE, json_models=[model])
    assert plan.message_source == "template"
    assert plan.invoice_note == EN_INVOICE


async def test_model_failure_uses_template() -> None:
    model = FakeJsonModel(error=RuntimeError("quota"))
    plan = await plan_chase(member(), cycle(), score(), now=LATE, json_models=[model])
    assert plan.message_source == "template"
    assert plan.invoice_note == EN_INVOICE


async def test_arabic_template_for_ar_locale() -> None:
    plan = await plan_chase(member(locale="ar-EG"), cycle(), score(), now=LATE, json_models=[])
    assert plan.locale == "ar-EG"
    assert plan.invoice_note == (
        "أهلاً Omar، الفاتورة دي عن قسط الجمعية $100.00 اللي لسه مدفعش للدورة 3 في Family Circle."
    )


async def test_member_message_never_contains_reliability_label() -> None:
    leaky = "Hi Omar, because of Paid late recently we are writing about $100.00."
    model = FakeJsonModel(ChaseText(text=leaky))
    plan = await plan_chase(member(), cycle(), score(0.7, LABELLED), now=LATE, json_models=[model])
    assert plan.invoice_note == EN_INVOICE
    assert "paid late recently" not in (plan.invoice_note or "").lower()
    assert plan.agent_decision is not None
    assert "Paid late recently" in plan.agent_decision.rationale


async def test_prompt_never_carries_reliability_data() -> None:
    model = FakeJsonModel(ChaseText(text="Hi Omar, about your $100.00 for cycle 3."))
    await plan_chase(member(), cycle(), score(0.7, LABELLED), now=LATE, json_models=[model])
    system, user = model.prompts[0]
    for text in (system, user):
        assert "Paid late recently" not in text
        assert "0.7" not in text
    assert "<untrusted_member_text>" in user


async def test_noop_makes_no_llm_call() -> None:
    model = FakeJsonModel(ChaseText(text="unused"))
    plan = await plan_chase(
        member(), cycle(), score(0.1), now=DUE - timedelta(days=1), json_models=[model]
    )
    assert plan.action is ChaseAction.NOOP
    assert model.prompts == []
