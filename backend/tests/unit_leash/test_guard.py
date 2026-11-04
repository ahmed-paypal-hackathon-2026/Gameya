"""Table-driven tests for the pure guard."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.leash.guard import evaluate_rules
from app.schemas.contracts import (
    GuardAction,
    LeashDecision,
    LeashPolicy,
    LeashThresholds,
)

NOW = "2026-10-28T12:00:00+00:00"
ALLOW, ASK, DENY = LeashDecision.ALLOW, LeashDecision.ASK, LeashDecision.DENY
PO = GuardAction.RELEASE_POT_PAYOUT
CH = GuardAction.SEND_CHASE_INVOICE
RM = GuardAction.SEND_INVOICE_REMINDER
SW = GuardAction.APPLY_SLOT_SWAP


def base(action: GuardAction) -> dict[str, Any]:
    """Clean facts for an action: every check passes."""
    common: dict[str, Any] = {"now": NOW, "circle_id": "c1", "cycle_index": 2}
    if action is PO:
        return common | {
            "payout_id": "p1",
            "amount": "300.00",
            "recipient_member_id": "m1",
            "recipient_name": "Amina",
            "recipient_payout_email": "amina@x.com",
            "payout_email_changed_at": None,
            "payer_emails": ["amina@x.com", "b@x.com"],
            "reserve_cover_used": False,
            "slot_swapped_at": None,
            "recipient_is_organiser": False,
            "recipient_has_unpaid": False,
        }
    if action is CH:
        return common | {
            "member_id": "m2",
            "member_name": "Omar",
            "invoice_amount": "100.00",
            "unpaid_amount": "100.00",
            "due_at": "2026-10-20T12:00:00+00:00",
            "open_invoice_count": 0,
            "last_invoice_sent_at": None,
            "no_auto_chase": False,
        }
    if action is RM:
        return common | {
            "member_id": "m2",
            "member_name": "Omar",
            "last_reminder_sent_at": None,
            "reminders_sent_this_cycle": 0,
        }
    return common | {
        "member_a_name": "Amina",
        "member_b_name": "Omar",
        "slot_a_payout_at": "2026-12-01T00:00:00+00:00",
        "slot_b_payout_at": "2027-01-01T00:00:00+00:00",
        "moved_earlier_member_name": "Omar",
        "moved_earlier_p_late": "0.10",
    }


def policy(preset: str = "STANDARD") -> LeashPolicy:
    return LeashPolicy.model_validate(
        {
            "circle_id": "c1",
            "version": 1,
            "preset": preset,
            "thresholds": LeashThresholds().model_dump(),
        }
    )


CHANGED_1D = "2026-10-27T12:00:00+00:00"
FAR_DUE = "2026-11-10T00:00:00+00:00"
NEAR = "2026-10-29T00:00:00+00:00"

CASES: list[tuple[str, GuardAction, dict[str, Any], LeashDecision, list[str]]] = [
    # --- payout ---
    ("STANDARD", PO, {}, ALLOW, ["PO-00"]),
    ("STANDARD", PO, {"payout_email_changed_at": "2026-10-21T13:00:00+00:00"}, ASK, ["PO-01"]),
    ("STANDARD", PO, {"payout_email_changed_at": "2026-10-21T12:00:00+00:00"}, ALLOW, ["PO-00"]),
    ("STANDARD", PO, {"payout_email_changed_at": "2026-10-10T12:00:00+00:00"}, ALLOW, ["PO-00"]),
    ("STANDARD", PO, {"recipient_payout_email": "zed@x.com"}, ASK, ["PO-02"]),
    ("STANDARD", PO, {"payer_emails": []}, ASK, ["PO-02"]),
    ("STANDARD", PO, {"reserve_cover_used": True}, ASK, ["PO-03"]),
    ("STANDARD", PO, {"slot_swapped_at": "2026-10-28T00:00:00+00:00"}, ASK, ["PO-04"]),
    ("STANDARD", PO, {"slot_swapped_at": CHANGED_1D}, ALLOW, ["PO-00"]),
    ("STANDARD", PO, {"slot_swapped_at": "2026-10-20T12:00:00+00:00"}, ALLOW, ["PO-00"]),
    ("STANDARD", PO, {"recipient_is_organiser": True}, ASK, ["PO-05"]),
    ("STANDARD", PO, {"recipient_has_unpaid": True}, ASK, ["PO-06"]),
    (
        "STANDARD",
        PO,
        {"payout_email_changed_at": CHANGED_1D, "recipient_has_unpaid": True},
        ASK,
        ["PO-01", "PO-06"],
    ),
    (
        "STANDARD",
        PO,
        {
            "reserve_cover_used": True,
            "recipient_is_organiser": True,
            "recipient_payout_email": "q@q.q",
        },
        ASK,
        ["PO-02", "PO-03", "PO-05"],
    ),
    ("STANDARD", PO, {"approved_rule_ids": ["PO-01"]}, ALLOW, ["PO-00"]),
    (
        "STANDARD",
        PO,
        {"payout_email_changed_at": CHANGED_1D, "approved_rule_ids": ["PO-01"]},
        ALLOW,
        ["PO-00"],
    ),
    (
        "STANDARD",
        PO,
        {
            "payout_email_changed_at": CHANGED_1D,
            "recipient_has_unpaid": True,
            "approved_rule_ids": ["PO-01"],
        },
        ASK,
        ["PO-06"],
    ),
    ("HANDS_OFF", PO, {}, ALLOW, ["PO-00"]),
    ("HANDS_OFF", PO, {"reserve_cover_used": True}, ASK, ["PO-03"]),
    ("STRICT", PO, {}, ASK, ["PO-00", "STRICT"]),
    ("STRICT", PO, {"reserve_cover_used": True}, ASK, ["PO-03"]),
    ("STRICT", PO, {"approved_rule_ids": ["STRICT"]}, ALLOW, ["PO-00"]),
    (
        "STRICT",
        PO,
        {"reserve_cover_used": True, "approved_rule_ids": ["PO-03"]},
        ASK,
        ["PO-00", "STRICT"],
    ),
    # --- chase ---
    ("STANDARD", CH, {}, ALLOW, ["G-01"]),
    ("STANDARD", CH, {"unpaid_amount": "40.00"}, DENY, ["G-04"]),
    ("STANDARD", CH, {"open_invoice_count": 1}, DENY, ["G-02"]),
    ("STANDARD", CH, {"last_invoice_sent_at": CHANGED_1D}, DENY, ["G-02"]),
    ("STANDARD", CH, {"last_invoice_sent_at": "2026-10-20T12:00:00+00:00"}, ALLOW, ["G-01"]),
    ("STANDARD", CH, {"no_auto_chase": True}, ASK, ["G-03"]),
    ("STANDARD", CH, {"unpaid_amount": "40.00", "no_auto_chase": True}, DENY, ["G-04"]),
    (
        "STANDARD",
        CH,
        {"unpaid_amount": "40.00", "open_invoice_count": 2},
        DENY,
        ["G-04", "G-02"],
    ),
    ("STANDARD", CH, {"due_at": CHANGED_1D}, ALLOW, ["G-01"]),
    ("STANDARD", CH, {"due_at": "2026-10-27T12:00:01+00:00"}, ASK, ["DEFAULT-ASK"]),
    ("STANDARD", CH, {"due_at": FAR_DUE}, ASK, ["DEFAULT-ASK"]),
    (
        "STANDARD",
        CH,
        {"due_at": FAR_DUE, "approved_rule_ids": ["DEFAULT-ASK"]},
        ALLOW,
        ["DEFAULT-ASK"],
    ),
    ("STANDARD", CH, {"no_auto_chase": True, "approved_rule_ids": ["G-03"]}, ALLOW, ["G-01"]),
    (
        "STANDARD",
        CH,
        {"no_auto_chase": True, "due_at": FAR_DUE, "approved_rule_ids": ["G-03"]},
        ASK,
        ["DEFAULT-ASK"],
    ),
    ("STANDARD", CH, {"unpaid_amount": "40.00", "approved_rule_ids": ["G-04"]}, DENY, ["G-04"]),
    ("STANDARD", CH, {"open_invoice_count": 1, "approved_rule_ids": ["G-02"]}, DENY, ["G-02"]),
    ("HANDS_OFF", CH, {}, ALLOW, ["G-01"]),
    ("STRICT", CH, {}, ASK, ["G-01", "STRICT"]),
    ("STRICT", CH, {"approved_rule_ids": ["STRICT"]}, ALLOW, ["G-01"]),
    ("STRICT", CH, {"unpaid_amount": "40.00"}, DENY, ["G-04"]),
    # --- reminder ---
    ("STANDARD", RM, {}, ALLOW, ["G-05"]),
    ("STANDARD", RM, {"reminders_sent_this_cycle": 2}, ALLOW, ["G-05"]),
    ("STANDARD", RM, {"reminders_sent_this_cycle": 3}, DENY, ["G-05"]),
    ("STANDARD", RM, {"last_reminder_sent_at": CHANGED_1D}, DENY, ["G-05"]),
    ("STANDARD", RM, {"last_reminder_sent_at": "2026-10-26T12:00:00+00:00"}, ALLOW, ["G-05"]),
    (
        "STANDARD",
        RM,
        {"reminders_sent_this_cycle": 3, "approved_rule_ids": ["G-05"]},
        DENY,
        ["G-05"],
    ),
    ("STRICT", RM, {}, ALLOW, ["G-05"]),
    ("STRICT", RM, {"reminders_sent_this_cycle": 3}, DENY, ["G-05"]),
    ("HANDS_OFF", RM, {"reminders_sent_this_cycle": 3}, DENY, ["G-05"]),
    # --- swap ---
    ("STANDARD", SW, {}, ALLOW, ["S-00"]),
    ("STANDARD", SW, {"slot_a_payout_at": "2026-10-30T12:00:00+00:00"}, ASK, ["S-01"]),
    ("STANDARD", SW, {"slot_b_payout_at": "2026-10-28T12:00:00+00:00"}, ASK, ["S-01"]),
    ("STANDARD", SW, {"slot_a_payout_at": "2026-10-30T12:00:01+00:00"}, ALLOW, ["S-00"]),
    ("STANDARD", SW, {"slot_a_payout_at": "2026-10-28T11:59:59+00:00"}, ALLOW, ["S-00"]),
    ("STANDARD", SW, {"moved_earlier_p_late": "0.50"}, ASK, ["S-02"]),
    ("STANDARD", SW, {"moved_earlier_p_late": "0.49"}, ALLOW, ["S-00"]),
    ("STANDARD", SW, {"moved_earlier_p_late": None}, ALLOW, ["S-00"]),
    (
        "STANDARD",
        SW,
        {"slot_a_payout_at": NEAR, "moved_earlier_p_late": "0.90"},
        ASK,
        ["S-01", "S-02"],
    ),
    (
        "STANDARD",
        SW,
        {"slot_a_payout_at": NEAR, "approved_rule_ids": ["S-01"]},
        ALLOW,
        ["S-00"],
    ),
    ("STRICT", SW, {}, ALLOW, ["S-00"]),
    ("HANDS_OFF", SW, {"moved_earlier_p_late": "0.90"}, ASK, ["S-02"]),
]


@pytest.mark.parametrize(("preset", "action", "over", "decision", "rule_ids"), CASES)
def test_rule_table(
    preset: str,
    action: GuardAction,
    over: dict[str, Any],
    decision: LeashDecision,
    rule_ids: list[str],
) -> None:
    facts = base(action) | over
    assert evaluate_rules(policy(preset), action, facts) == (decision, rule_ids)


def test_case_count() -> None:
    assert len(CASES) >= 40


def test_custom_threshold_changes_window() -> None:
    p = LeashPolicy(
        circle_id="c1", version=1, thresholds=LeashThresholds(email_change_window_days=14)
    )
    facts = base(PO) | {"payout_email_changed_at": "2026-10-20T12:00:00+00:00"}
    assert evaluate_rules(p, PO, facts) == (ASK, ["PO-01"])


def test_naive_datetime_treated_as_utc() -> None:
    facts = base(PO) | {"payout_email_changed_at": "2026-10-27T12:00:00"}
    assert evaluate_rules(policy(), PO, facts) == (ASK, ["PO-01"])


def test_email_case_and_space_insensitive() -> None:
    facts = base(PO) | {"recipient_payout_email": " Amina@X.com ", "payer_emails": ["amina@x.com"]}
    assert evaluate_rules(policy(), PO, facts) == (ALLOW, ["PO-00"])


def test_equal_money_different_scale() -> None:
    facts = base(CH) | {"invoice_amount": "100", "unpaid_amount": "100.00"}
    assert evaluate_rules(policy(), CH, facts) == (ALLOW, ["G-01"])


def test_datetime_objects_and_decimal_accepted() -> None:
    facts = base(CH) | {
        "now": datetime(2026, 10, 28, 12, tzinfo=UTC),
        "invoice_amount": Decimal(100),
        "unpaid_amount": 100,
    }
    assert evaluate_rules(policy(), CH, facts) == (ALLOW, ["G-01"])


def listed_policy(preset: str, member_ids: list[str]) -> LeashPolicy:
    return policy(preset).model_copy(update={"no_auto_chase_member_ids": member_ids})


def test_policy_no_auto_chase_list_asks_g03() -> None:
    p = listed_policy("STANDARD", ["m2"])
    assert evaluate_rules(p, CH, base(CH)) == (ASK, ["G-03"])


def test_policy_no_auto_chase_list_other_member_unchanged() -> None:
    p = listed_policy("STANDARD", ["m9"])
    assert evaluate_rules(p, CH, base(CH)) == (ALLOW, ["G-01"])


def test_strict_approved_default_ask_still_asks_strict() -> None:
    # DEFAULT-ASK waived -> ALLOW, then STRICT turns every allowed money action into an ASK.
    facts = base(CH) | {"due_at": FAR_DUE, "approved_rule_ids": ["DEFAULT-ASK"]}
    assert evaluate_rules(policy("STRICT"), CH, facts) == (ASK, ["DEFAULT-ASK", "STRICT"])


def test_strict_default_ask_and_strict_both_approved_allows() -> None:
    facts = base(CH) | {"due_at": FAR_DUE, "approved_rule_ids": ["DEFAULT-ASK", "STRICT"]}
    assert evaluate_rules(policy("STRICT"), CH, facts) == (ALLOW, ["DEFAULT-ASK"])


def test_g03_with_g02_denies() -> None:
    facts = base(CH) | {"no_auto_chase": True, "open_invoice_count": 1}
    assert evaluate_rules(policy(), CH, facts) == (DENY, ["G-02"])


def test_hands_off_chase_no_auto_chase_asks() -> None:
    facts = base(CH) | {"no_auto_chase": True}
    assert evaluate_rules(policy("HANDS_OFF"), CH, facts) == (ASK, ["G-03"])
