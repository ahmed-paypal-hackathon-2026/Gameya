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
