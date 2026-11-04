"""Template reasons for Leash decisions.

Pure: no I/O, no LLM. Every number in a reason comes from the facts (money,
or whole days / hours derived from fact datetimes).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from ..schemas.contracts import GuardAction, LeashDecision
from .facts import (
    BaseFacts,
    ChaseInvoiceFacts,
    PayoutFacts,
    ReminderFacts,
    SwapFacts,
)

MAX_REASON_LEN = 200
_STRICT = "STRICT"
_ALLOWED = "Allowed:"
_STRICT_PREFIX = "Waiting (Strict preset):"


def _money(value: Decimal) -> str:
    return f"${value:.2f}"


def _when(now: datetime, then: datetime | None) -> str:
    if then is None:
        raise ValueError("missing timestamp for a {when} reason")
    days = max((now - then).days, 0)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _hours_to_payout(f: SwapFacts) -> int:
    upcoming = [t for t in (f.slot_a_payout_at, f.slot_b_payout_at) if t >= f.now]
    if not upcoming:
        raise ValueError("no upcoming slot payout for an {hours} reason")
    seconds = (min(upcoming) - f.now).total_seconds()
    return int(-(-seconds // 3600))  # ceiling, so "within N h" is true


def _payout(f: BaseFacts) -> PayoutFacts:
    if not isinstance(f, PayoutFacts):
        raise TypeError("payout rule needs PayoutFacts")
    return f


def _chase(f: BaseFacts) -> ChaseInvoiceFacts:
    if not isinstance(f, ChaseInvoiceFacts):
        raise TypeError("chase rule needs ChaseInvoiceFacts")
    return f


def _reminder(f: BaseFacts) -> ReminderFacts:
    if not isinstance(f, ReminderFacts):
        raise TypeError("reminder rule needs ReminderFacts")
    return f


def _swap(f: BaseFacts) -> SwapFacts:
    if not isinstance(f, SwapFacts):
        raise TypeError("swap rule needs SwapFacts")
    return f


def _po00(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Allowed: all payout checks clear (PO-00). "
        f"Releasing {_money(p.amount)} to {p.recipient_name}."
    )


def _po01(f: BaseFacts) -> str:
    p = _payout(f)
    n = p.recipient_name
    return (
        f"Held: {n}'s payout email changed {_when(p.now, p.payout_email_changed_at)} "
        f"(rule PO-01). Confirm it with {n} before releasing {_money(p.amount)}."
    )


def _po02(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Held: {p.recipient_name}'s payout email has never paid into this circle "
        f"(rule PO-02). Confirm it before releasing {_money(p.amount)}."
    )


def _po03(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Held: this pot used reserve cover (rule PO-03). "
        f"Review before releasing {_money(p.amount)} to {p.recipient_name}."
    )


def _po04(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Held: {p.recipient_name}'s slot changed by a swap "
        f"{_when(p.now, p.slot_swapped_at)} (rule PO-04). "
        f"Cooling-off before releasing {_money(p.amount)}."
    )


def _po05(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Held: {p.recipient_name} is the organiser (rule PO-05). Approval is logged for members."
    )


def _po06(f: BaseFacts) -> str:
    p = _payout(f)
    return (
        f"Held: {p.recipient_name} still has an unpaid contribution (rule PO-06). "
        f"Decide before releasing {_money(p.amount)}."
    )


def _g01(f: BaseFacts) -> str:
    c = _chase(f)
    return f"Allowed: invoice for {c.member_name}'s unpaid {_money(c.invoice_amount)} (rule G-01)."


def _g02(f: BaseFacts) -> str:
    c = _chase(f)
    return f"Denied: {c.member_name} already has an open or recent invoice this cycle (rule G-02)."


def _g03(f: BaseFacts) -> str:
    c = _chase(f)
    return (
        f"Waiting: {c.member_name} is tagged no auto-chase (rule G-03). "
        f"Approve to send {_money(c.invoice_amount)} invoice."
    )


def _g04(f: BaseFacts) -> str:
    c = _chase(f)
    return (
        f"Denied: invoice {_money(c.invoice_amount)} does not match unpaid "
        f"{_money(c.unpaid_amount)} (rule G-04)."
    )


def _default_ask(f: BaseFacts) -> str:
    c = _chase(f)
    return f"Waiting: no rule allows this invoice to {c.member_name} yet. Approve to send."


def _s00(f: BaseFacts) -> str:
    s = _swap(f)
    return f"Allowed: swap between {s.member_a_name} and {s.member_b_name} (rule S-00)."


def _s01(f: BaseFacts) -> str:
    s = _swap(f)
    return f"Waiting: a swapped slot pays out within {_hours_to_payout(s)} h (rule S-01)."


def _s02(f: BaseFacts) -> str:
    s = _swap(f)
    return f"Waiting: swap moves high-risk {s.moved_earlier_member_name} earlier (rule S-02)."


_TEMPLATES: dict[str, Callable[[BaseFacts], str]] = {
    "PO-00": _po00,
    "PO-01": _po01,
    "PO-02": _po02,
    "PO-03": _po03,
    "PO-04": _po04,
    "PO-05": _po05,
    "PO-06": _po06,
    "G-01": _g01,
    "G-02": _g02,
    "G-03": _g03,
    "G-04": _g04,
    "DEFAULT-ASK": _default_ask,
    "S-00": _s00,
    "S-01": _s01,
    "S-02": _s02,
}


def _render_one(rule_id: str, decision: LeashDecision, facts: BaseFacts) -> str:
    if rule_id == "LEASH_DISABLED":
        return "Allowed: Leash is off."
    if rule_id == "G-05":
        r = _reminder(facts)
        if decision is LeashDecision.ALLOW:
            return f"Allowed: reminder to {r.member_name} (rule G-05)."
        return f"Denied: reminder to {r.member_name} too soon or limit reached (rule G-05)."
    template = _TEMPLATES.get(rule_id)
    if template is None:
        raise ValueError(f"no reason template for rule id {rule_id!r}")
    return template(facts)


def render_reason(
    action: GuardAction,
    decision: LeashDecision,
    rule_ids: list[str],
    facts: BaseFacts,
) -> str:
    """Render a human reason (at most 200 chars) for a decision and its rule ids."""
    strict = bool(rule_ids) and rule_ids[-1] == _STRICT
    ids = rule_ids[:-1] if strict else list(rule_ids)
    if not ids:
        raise ValueError("render_reason needs at least one rule id")
    text = _render_one(ids[0], decision, facts)
    if strict and text.startswith(_ALLOWED):
        text = _STRICT_PREFIX + text[len(_ALLOWED) :]
    elif strict:
        text = f"{_STRICT_PREFIX} {text}"
    if not strict and len(ids) > 1:
        text += f" (+{len(ids) - 1} more: {', '.join(ids[1:])})"
    if len(text) > MAX_REASON_LEN:
        text = text[: MAX_REASON_LEN - 1] + "…"
    return text
