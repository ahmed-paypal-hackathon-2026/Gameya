"""Tests for template reasons."""

import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.leash.facts import (
    BaseFacts,
    ChaseInvoiceFacts,
    PayoutFacts,
    ReminderFacts,
    SwapFacts,
)
from app.leash.templates import render_reason
from app.schemas.contracts import GuardAction, LeashDecision

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
ALLOW, ASK, DENY = LeashDecision.ALLOW, LeashDecision.ASK, LeashDecision.DENY
PO = GuardAction.RELEASE_POT_PAYOUT
CH = GuardAction.SEND_CHASE_INVOICE
RM = GuardAction.SEND_INVOICE_REMINDER
SW = GuardAction.APPLY_SLOT_SWAP


def payout(name: str = "Amina", **kw: Any) -> PayoutFacts:
    data: dict[str, Any] = {
        "now": NOW,
        "circle_id": "c1",
        "payout_id": "p1",
        "amount": "600.00",
        "recipient_member_id": "m1",
        "recipient_name": name,
        "recipient_payout_email": "a@x.com",
        "payout_email_changed_at": NOW - timedelta(days=1),
        "payer_emails": ["a@x.com"],
        "reserve_cover_used": False,
        "slot_swapped_at": NOW - timedelta(days=3, hours=2),
        "recipient_is_organiser": False,
        "recipient_has_unpaid": False,
    }
    return PayoutFacts(**(data | kw))


def chase(name: str = "Omar") -> ChaseInvoiceFacts:
    return ChaseInvoiceFacts(
        now=NOW,
        circle_id="c1",
        member_id="m2",
        member_name=name,
        invoice_amount="100.00",
        unpaid_amount="90.00",
        due_at=NOW - timedelta(days=8),
        open_invoice_count=0,
        no_auto_chase=False,
    )


def reminder(name: str = "Omar") -> ReminderFacts:
    return ReminderFacts(
        now=NOW, circle_id="c1", member_id="m2", member_name=name, reminders_sent_this_cycle=1
    )


def swap(name: str = "Omar") -> SwapFacts:
    return SwapFacts(
        now=NOW,
        circle_id="c1",
        member_a_name=name,
        member_b_name=name + "b",
        slot_a_payout_at=NOW + timedelta(days=9),
        slot_b_payout_at=NOW + timedelta(hours=30, minutes=40),
        moved_earlier_member_name=name,
        moved_earlier_p_late="0.60",
    )


def test_po01_amina_yesterday() -> None:
    reason = render_reason(PO, ASK, ["PO-01"], payout())
    assert reason == (
        "Held: Amina's payout email changed yesterday (rule PO-01). "
        "Confirm it with Amina before releasing $600.00."
    )


def test_po00_and_strict() -> None:
    assert render_reason(PO, ALLOW, ["PO-00"], payout()) == (
        "Allowed: all payout checks clear (PO-00). Releasing $600.00 to Amina."
    )
    assert render_reason(PO, ASK, ["PO-00", "STRICT"], payout()) == (
        "Waiting (Strict preset): all payout checks clear (PO-00). Releasing $600.00 to Amina."
    )


def test_when_phrases() -> None:
    today = payout(payout_email_changed_at=NOW)
    assert "changed today" in render_reason(PO, ASK, ["PO-01"], today)
    old = payout(payout_email_changed_at=NOW - timedelta(days=5, hours=23))
    assert "changed 5 days ago" in render_reason(PO, ASK, ["PO-01"], old)
    assert "swap 3 days ago" in render_reason(PO, ASK, ["PO-04"], payout())


def test_multi_rule_suffix() -> None:
    reason = render_reason(PO, ASK, ["PO-01", "PO-06"], payout())
    assert reason.endswith("(+1 more: PO-06)")
    reason = render_reason(PO, ASK, ["PO-01", "PO-03", "PO-06"], payout())
    assert reason.endswith("(+2 more: PO-03, PO-06)")


def test_strict_adds_no_suffix() -> None:
    reason = render_reason(CH, ASK, ["G-01", "STRICT"], chase())
    assert reason == "Waiting (Strict preset): invoice for Omar's unpaid $100.00 (rule G-01)."


def test_disabled() -> None:
    assert render_reason(PO, ALLOW, ["LEASH_DISABLED"], payout()) == "Allowed: Leash is off."


def test_chase_reminder_swap_copy() -> None:
    assert render_reason(CH, ALLOW, ["G-01"], chase()) == (
        "Allowed: invoice for Omar's unpaid $100.00 (rule G-01)."
    )
    assert render_reason(CH, DENY, ["G-04"], chase()) == (
        "Denied: invoice $100.00 does not match unpaid $90.00 (rule G-04)."
    )
    assert render_reason(CH, ASK, ["DEFAULT-ASK"], chase()) == (
        "Waiting: no rule allows this invoice to Omar yet. Approve to send."
    )
    assert (
        render_reason(RM, ALLOW, ["G-05"], reminder()) == "Allowed: reminder to Omar (rule G-05)."
    )
    assert render_reason(RM, DENY, ["G-05"], reminder()) == (
        "Denied: reminder to Omar too soon or limit reached (rule G-05)."
    )
    assert render_reason(SW, ALLOW, ["S-00"], swap()) == (
        "Allowed: swap between Omar and Omarb (rule S-00)."
    )
    assert render_reason(SW, ASK, ["S-01"], swap()) == (
        "Waiting: a swapped slot pays out within 31 h (rule S-01)."
    )
    assert render_reason(SW, ASK, ["S-02"], swap()) == (
        "Waiting: swap moves high-risk Omar earlier (rule S-02)."
    )


def _cases(name: str) -> list[tuple[GuardAction, LeashDecision, str, BaseFacts]]:
    cases: list[tuple[GuardAction, LeashDecision, str, BaseFacts]] = [
        (PO, ASK, rid, payout(name))
        for rid in ["PO-00", "PO-01", "PO-02", "PO-03", "PO-04", "PO-05", "PO-06"]
    ]
    cases += [
        (CH, ASK, rid, chase(name)) for rid in ["G-01", "G-02", "G-03", "G-04", "DEFAULT-ASK"]
    ]
    cases += [(RM, d, "G-05", reminder(name)) for d in (ALLOW, DENY)]
    cases += [(SW, ASK, rid, swap(name)) for rid in ["S-00", "S-01", "S-02"]]
    cases.append((PO, ALLOW, "LEASH_DISABLED", payout(name)))
    return cases


@pytest.mark.parametrize(("action", "decision", "rid", "facts"), _cases("N" * 60))
def test_every_rule_fits_200(
    action: GuardAction, decision: LeashDecision, rid: str, facts: BaseFacts
) -> None:
    ids = [rid, "PO-06", "PO-05"]
    assert len(render_reason(action, decision, [rid], facts)) <= 200
    assert len(render_reason(action, decision, ids, facts)) <= 200
    assert len(render_reason(action, decision, [rid, "STRICT"], facts)) <= 200


def _fact_tokens(facts: BaseFacts) -> set[str]:
    """Whole numeric tokens the facts legitimately contain (money as 0.00, ints)."""
    tokens: set[str] = set()
    for value in facts.model_dump().values():
        if isinstance(value, Decimal):
            tokens.add(f"{value:.2f}")
        elif isinstance(value, int) and not isinstance(value, bool):
            tokens.add(str(value))
    return tokens


def _derived_tokens(facts: BaseFacts) -> set[str]:
    """Day / hour counts derived from this case's own datetimes."""
    tokens: set[str] = set()
    for field in ("payout_email_changed_at", "slot_swapped_at"):
        then = getattr(facts, field, None)
        if then is not None:
            days = (facts.now - then).days
            if days >= 2:
                tokens.add(str(days))
    if isinstance(facts, SwapFacts):
        upcoming = [t for t in (facts.slot_a_payout_at, facts.slot_b_payout_at) if t >= facts.now]
        tokens.add(str(math.ceil((min(upcoming) - facts.now).total_seconds() / 3600)))
    return tokens


def _numbers(reason: str) -> list[str]:
    stripped = re.sub(r"\b(?:PO|G|S)-\d+\b", "", reason)
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", stripped)


@pytest.mark.parametrize(("action", "decision", "rid", "facts"), _cases("Sara"))
def test_numbers_come_from_facts(
    action: GuardAction, decision: LeashDecision, rid: str, facts: BaseFacts
) -> None:
    reason = render_reason(action, decision, [rid], facts)
    allowed = _fact_tokens(facts) | _derived_tokens(facts)
    for num in _numbers(reason):
        assert num in allowed, (rid, num, reason)


def test_numbers_check_catches_a_wrong_count() -> None:
    facts = payout(slot_swapped_at=NOW - timedelta(days=3, hours=2))
    good = render_reason(PO, ASK, ["PO-04"], facts)
    bad = good.replace("3 days ago", "10 days ago")
    allowed = _fact_tokens(facts) | _derived_tokens(facts)
    assert all(n in allowed for n in _numbers(good))
    assert not all(n in allowed for n in _numbers(bad))


def test_empty_rule_ids_raise() -> None:
    with pytest.raises(ValueError, match="at least one"):
        render_reason(PO, ALLOW, [], payout())
    with pytest.raises(ValueError, match="at least one"):
        render_reason(PO, ASK, ["STRICT"], payout())


def test_unknown_rule_id_raises() -> None:
    with pytest.raises(ValueError, match="PO-99"):
        render_reason(PO, ASK, ["PO-99"], payout())


def test_missing_timestamp_raises() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        render_reason(PO, ASK, ["PO-01"], payout(payout_email_changed_at=None))
    with pytest.raises(ValueError, match="timestamp"):
        render_reason(PO, ASK, ["PO-04"], payout(slot_swapped_at=None))


def test_no_upcoming_payout_raises() -> None:
    past = swap().model_copy(
        update={
            "slot_a_payout_at": NOW - timedelta(hours=1),
            "slot_b_payout_at": NOW - timedelta(hours=2),
        }
    )
    with pytest.raises(ValueError, match="upcoming"):
        render_reason(SW, ASK, ["S-01"], past)


def test_realistic_names_are_not_truncated() -> None:
    name = "Abdelrahman Mohamed El-Sayed Ali"[:30]
    reason = render_reason(PO, ASK, ["PO-01"], payout(name))
    assert "…" not in reason
    assert reason.endswith("before releasing $600.00.")
