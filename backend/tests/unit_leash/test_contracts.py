from decimal import Decimal

import pytest
from app.schemas.contracts import (
    GuardResult,
    LeashDecision,
    LeashThresholds,
)
from pydantic import ValidationError


def test_threshold_defaults():
    t = LeashThresholds()
    assert (
        t.email_change_window_days,
        t.reminder_min_gap_hours,
        t.max_reminders_per_cycle,
        t.swap_cooldown_hours,
        t.swap_near_payout_hours,
        t.high_risk_p_late,
    ) == (7, 48, 3, 24, 48, Decimal("0.50"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("reminder_min_gap_hours", 11),
        ("email_change_window_days", 31),
        ("high_risk_p_late", Decimal("1.01")),
    ],
)
def test_threshold_out_of_range_rejected(field, value):
    with pytest.raises(ValidationError):
        LeashThresholds(**{field: value})


def test_reason_max_200():
    with pytest.raises(ValidationError):
        GuardResult(
            decision=LeashDecision.ALLOW, rule_ids=[], reason="x" * 201, decision_id="lsd_1"
        )
