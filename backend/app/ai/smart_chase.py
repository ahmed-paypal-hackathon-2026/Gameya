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

from pydantic import BaseModel

from ..schemas.contracts import (
    AgentName,
    ChaseAction,
    ChasePlan,
    ReliabilityScore,
)
from .guardrails import build_decision, cap_amount, prose_numbers_ok, wrap_untrusted
from .ids import new_id
from .inputs import CycleDue, MemberDue
from .llm import JsonModel, default_models, run_with_failover
from .prompts.smart_chase import SYSTEM_PROMPT, TEMPLATES, ChaseKind

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
LLM_BUDGET_SECONDS = 6.0
LLM_PROVIDER_TIMEOUT_SECONDS = 5.0
MAX_MESSAGE_CHARS = 600
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


class ChaseText(BaseModel):
    text: str


async def _model_message(
    kind: ChaseKind,
    member: MemberDue,
    cycle: CycleDue,
    amount: Decimal,
    score: ReliabilityScore,
    models: Sequence[JsonModel],
) -> tuple[str, str] | None:
    """One LLM call. Returns (text, model name) only if the text passes every check."""
    due = cycle.due_at.date()
    prompt = "\n".join(
        [
            f"Locale: {member.locale}",
            f"Message type: {kind}",
            f"Member name: {wrap_untrusted(member.name, max_chars=80)}",
            f"Circle name: {wrap_untrusted(cycle.circle_name, max_chars=80)}",
            f"Amount: ${amount:.2f}",
            f"Due date: {due.isoformat()}",
            f"Cycle: {cycle.cycle_index}",
        ]
    )
    answered: list[str] = []

    async def attempt(model: JsonModel, _timeout: float) -> ChaseText:
        result = await model.generate_json(SYSTEM_PROMPT, prompt, ChaseText)
        answered.append(model.name)
        return result

    result = await run_with_failover(
        models,
        attempt,
        purpose="smart_chase.message",
        budget_seconds=LLM_BUDGET_SECONDS,
        provider_timeout=LLM_PROVIDER_TIMEOUT_SECONDS,
    )
    if result is None:
        return None
    text = result.text.strip()
    allowed = [f"{amount:.2f}", f"{due.year:04d}", f"{due.month:02d}", f"{due.day:02d}"]
    allowed.append(str(cycle.cycle_index))
    lowered = text.lower()
    if (
        not text
        or len(text) > MAX_MESSAGE_CHARS
        or not prose_numbers_ok(text, allowed)
        or any(f.label.lower() in lowered for f in score.top_factors)
    ):
        return None
    return text, answered[-1]


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
    models = json_models if json_models is not None else default_models()

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
            return await _build(
                ChaseAction.NUDGE,
                "NUDGE",
                member,
                cycle,
                score,
                member.unpaid_amount,
                common,
                models,
            )
        return noop("Not yet due and no nudge needed.")
    if now < due_at + GRACE_PERIOD:
        return noop("Inside the one-day grace period.")
    if member.open_invoice_id:
        return await _build(
            ChaseAction.REMINDER,
            "REMINDER",
            member,
            cycle,
            score,
            member.unpaid_amount,
            common,
            models,
        )
    amount = cap_amount(member.unpaid_amount, cycle.contribution_amount)
    return await _build(
        ChaseAction.INVOICE, "INVOICE", member, cycle, score, amount, common, models
    )


async def _build(
    action: ChaseAction,
    rule: str,
    member: MemberDue,
    cycle: CycleDue,
    score: ReliabilityScore,
    amount: Decimal,
    common: dict[str, Any],
    models: Sequence[JsonModel],
) -> ChasePlan:
    kind = _KIND[action]
    generated = await _model_message(kind, member, cycle, amount, score, models) if models else None
    message = generated[0] if generated else _template_message(kind, member, cycle, amount)
    fields: dict[str, Any] = {
        **common,
        "action": action,
        "plan_id": new_id("chp"),
        "contribution_id": member.contribution_id,
        "recipient_email": member.email,
        "locale": member.locale,
        "message_source": "gemini" if generated else "template",
        "agent_decision": build_decision(
            AgentName.SMART_CHASE,
            circle_id=cycle.circle_id,
            cycle_index=cycle.cycle_index,
            model=generated[1] if generated else TEMPLATE_MODEL,
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
