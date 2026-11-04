"""Pure, deterministic rule evaluation for Leash. No I/O, no LLM, no network."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..schemas.contracts import (
    GuardAction,
    LeashDecision,
    LeashPolicy,
    LeashThresholds,
)
from .facts import (
    FACTS_MODEL,
    ChaseInvoiceFacts,
    PayoutFacts,
    ReminderFacts,
    SwapFacts,
)

Match = tuple[str, LeashDecision]
# (matched rules in table order, fallback used when no matched rule survives waivers)
Evaluation = tuple[list[Match], Match]

_SEVERITY = {LeashDecision.ALLOW: 0, LeashDecision.ASK: 1, LeashDecision.DENY: 2}
_STRICT_ACTIONS = {GuardAction.RELEASE_POT_PAYOUT, GuardAction.SEND_CHASE_INVOICE}


def _payout(f: PayoutFacts, t: LeashThresholds) -> Evaluation:
    window = timedelta(days=t.email_change_window_days)
    cooldown = timedelta(hours=t.swap_cooldown_hours)
    checks = [
        (
            "PO-01",
            f.payout_email_changed_at is not None and f.payout_email_changed_at > f.now - window,
        ),
        ("PO-02", f.recipient_payout_email not in f.payer_emails),
        ("PO-03", f.reserve_cover_used),
        ("PO-04", f.slot_swapped_at is not None and f.slot_swapped_at > f.now - cooldown),
        ("PO-05", f.recipient_is_organiser),
        ("PO-06", f.recipient_has_unpaid),
    ]
    matched = [(rule, LeashDecision.ASK) for rule, fired in checks if fired]
    return matched, ("PO-00", LeashDecision.ALLOW)


def _chase(f: ChaseInvoiceFacts, policy: LeashPolicy) -> Evaluation:
    t = policy.thresholds
    gap = timedelta(hours=t.reminder_min_gap_hours)
    amounts_equal = f.invoice_amount == f.unpaid_amount
    recently_sent = f.last_invoice_sent_at is not None and f.last_invoice_sent_at > f.now - gap
    matched: list[Match] = []
    if not amounts_equal:
        matched.append(("G-04", LeashDecision.DENY))
    if f.open_invoice_count > 0 or recently_sent:
        matched.append(("G-02", LeashDecision.DENY))
    if f.no_auto_chase or f.member_id in policy.no_auto_chase_member_ids:
        matched.append(("G-03", LeashDecision.ASK))
    clean = amounts_equal and f.now >= f.due_at + timedelta(days=1) and f.open_invoice_count == 0
    fallback = ("G-01", LeashDecision.ALLOW) if clean else ("DEFAULT-ASK", LeashDecision.ASK)
    return matched, fallback


def _reminder(f: ReminderFacts, t: LeashThresholds) -> Evaluation:
    gap = timedelta(hours=t.reminder_min_gap_hours)
    too_soon = f.last_reminder_sent_at is not None and f.last_reminder_sent_at > f.now - gap
    too_many = f.reminders_sent_this_cycle >= t.max_reminders_per_cycle
    matched = [("G-05", LeashDecision.DENY)] if too_soon or too_many else []
    return matched, ("G-05", LeashDecision.ALLOW)


def _swap(f: SwapFacts, t: LeashThresholds) -> Evaluation:
    horizon = f.now + timedelta(hours=t.swap_near_payout_hours)
    near = any(f.now <= slot <= horizon for slot in (f.slot_a_payout_at, f.slot_b_payout_at))
    risky = f.moved_earlier_p_late is not None and f.moved_earlier_p_late >= t.high_risk_p_late
    matched = [
        (rule, LeashDecision.ASK) for rule, fired in (("S-01", near), ("S-02", risky)) if fired
    ]
    return matched, ("S-00", LeashDecision.ALLOW)


def _combine(
    policy: LeashPolicy,
    action: GuardAction,
    approved: list[str],
    evaluation: Evaluation,
) -> tuple[LeashDecision, list[str]]:
    matched, fallback = evaluation
    # Waivers: only ASK rules can be waived; DENY rules never are.
    remaining = [m for m in matched if not (m[1] is LeashDecision.ASK and m[0] in approved)]
    if remaining:
        decision = max((outcome for _, outcome in remaining), key=_SEVERITY.__getitem__)
        rule_ids = [rule for rule, outcome in remaining if outcome is decision]
    else:
        rule, decision = fallback
        if decision is LeashDecision.ASK and rule in approved:
            decision = LeashDecision.ALLOW
        rule_ids = [rule]

    if (
        policy.preset == "STRICT"
        and action in _STRICT_ACTIONS
        and decision is LeashDecision.ALLOW
        and "STRICT" not in approved
    ):
        return LeashDecision.ASK, [*rule_ids, "STRICT"]
    return decision, rule_ids


def evaluate_rules(
    policy: LeashPolicy, action: GuardAction, facts: dict[str, Any]
) -> tuple[LeashDecision, list[str]]:
    """Apply the Leash rule table to ``facts`` and return ``(decision, rule_ids)``."""
    parsed = FACTS_MODEL[action].model_validate(facts)
    t = policy.thresholds
    if isinstance(parsed, PayoutFacts):
        evaluation = _payout(parsed, t)
    elif isinstance(parsed, ChaseInvoiceFacts):
        evaluation = _chase(parsed, policy)
    elif isinstance(parsed, ReminderFacts):
        evaluation = _reminder(parsed, t)
    else:
        assert isinstance(parsed, SwapFacts)
        evaluation = _swap(parsed, t)
    return _combine(policy, action, parsed.approved_rule_ids, evaluation)
