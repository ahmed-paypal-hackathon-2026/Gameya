"""Smart Chase: decide whether a late member gets nothing, a nudge, an invoice or a reminder.

`plan_chase` is deterministic apart from the wording of the message. It makes no tool call.
`execute_chase` runs the invoice tools through the guarded executor; Person 1's scheduler
calls it only after Leash allows the plan or the organiser approves it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..schemas.contracts import (
    AgentName,
    ChaseAction,
    ChasePlan,
    ReliabilityScore,
)
from .guardrails import build_decision, cap_amount
from .ids import new_id
from .inputs import CycleDue, MemberDue
from .llm import JsonModel
from .prompts.smart_chase import TEMPLATES, ChaseKind

__all__ = [
    "GRACE_PERIOD",
    "HIGH_RISK_P_LATE",
    "NUDGE_WINDOW",
    "plan_chase",
    "risk_reasons",
]

HIGH_RISK_P_LATE = Decimal("0.50")
NUDGE_WINDOW = timedelta(days=3)
GRACE_PERIOD = timedelta(days=1)
DETERMINISTIC = "deterministic"
TEMPLATE_MODEL = "template"
_KIND: dict[ChaseAction, ChaseKind] = {
    ChaseAction.NUDGE: "nudge",
    ChaseAction.INVOICE: "invoice",
    ChaseAction.REMINDER: "reminder",
}


def risk_reasons(score: ReliabilityScore) -> list[str]:
    """Labels of the factors that raise the risk, in the given order.

    Private: they go into the decision rationale only, never into member-facing text.
    """
    return [f.label for f in score.top_factors if f.contribution > 0]


def _template_message(kind: ChaseKind, member: MemberDue, cycle: CycleDue, amount: Decimal) -> str:
    return TEMPLATES[(kind, member.locale)].format(
        name=member.name,
        amount=f"{amount:.2f}",
        cycle_index=cycle.cycle_index,
        due=cycle.due_at.date().isoformat(),
        circle=cycle.circle_name,
    )


def _rationale(rule: str, score: ReliabilityScore) -> str:
    reasons = risk_reasons(score)
    text = f"{rule} p_late={Decimal(str(score.p_late))}."
    if reasons:
        text += f" Risk factors: {'; '.join(reasons)}."
    return text


async def plan_chase(
    member: MemberDue,
    cycle: CycleDue,
    score: ReliabilityScore,
    *,
    now: datetime,
    json_models: Sequence[JsonModel] | None = None,
) -> ChasePlan:
    """Pick the chase action for one member. Never calls PayPal."""
    if now.tzinfo is None or cycle.due_at.tzinfo is None:
        raise ValueError("now and due_at must be timezone-aware")
    del json_models  # message wording from the model is added in the next commit

    due_at = cycle.due_at
    high_risk = Decimal(str(score.p_late)) >= HIGH_RISK_P_LATE
    common: dict[str, Any] = {"member_id": member.member_id, "cycle_index": cycle.cycle_index}

    def noop(rule: str) -> ChasePlan:
        return ChasePlan.model_validate(
            {
                **common,
                "action": ChaseAction.NOOP,
                "agent_decision": build_decision(
                    AgentName.SMART_CHASE,
                    circle_id=cycle.circle_id,
                    cycle_index=cycle.cycle_index,
                    model=DETERMINISTIC,
                    rationale=_rationale(rule, score),
                ),
            }
        )

    if member.unpaid_amount == 0:
        return noop("Nothing unpaid.")
    if now < due_at:
        if due_at - now <= NUDGE_WINDOW and high_risk:
            return _build(
                ChaseAction.NUDGE, "NUDGE", member, cycle, score, member.unpaid_amount, common
            )
        return noop("Not yet due and no nudge needed.")
    if now < due_at + GRACE_PERIOD:
        return noop("Inside the one-day grace period.")
    if member.open_invoice_id:
        return _build(
            ChaseAction.REMINDER,
            "REMINDER",
            member,
            cycle,
            score,
            member.unpaid_amount,
            common,
        )
    amount = cap_amount(member.unpaid_amount, cycle.contribution_amount)
    return _build(ChaseAction.INVOICE, "INVOICE", member, cycle, score, amount, common)


def _build(
    action: ChaseAction,
    rule: str,
    member: MemberDue,
    cycle: CycleDue,
    score: ReliabilityScore,
    amount: Decimal,
    common: dict[str, Any],
) -> ChasePlan:
    message = _template_message(_KIND[action], member, cycle, amount)
    fields: dict[str, Any] = {
        **common,
        "action": action,
        "plan_id": new_id("chp"),
        "contribution_id": member.contribution_id,
        "recipient_email": member.email,
        "locale": member.locale,
        "message_source": "template",
        "agent_decision": build_decision(
            AgentName.SMART_CHASE,
            circle_id=cycle.circle_id,
            cycle_index=cycle.cycle_index,
            model=TEMPLATE_MODEL,
            rationale=_rationale(f"{rule} chosen.", score),
        ),
    }
    if action is ChaseAction.NUDGE:
        fields["nudge_text"] = message
    else:
        fields["invoice_note"] = message
    if action is ChaseAction.INVOICE:
        fields["invoice_amount"] = amount
        fields["invoice_number"] = (
            f"GM-{member.contribution_id[-10:]}-R{member.invoice_attempts + 1}"
        )
    if action is ChaseAction.REMINDER:
        fields["invoice_id"] = member.open_invoice_id
    return ChasePlan.model_validate(fields)
