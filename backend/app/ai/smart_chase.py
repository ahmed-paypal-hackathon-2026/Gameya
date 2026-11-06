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

from pydantic import BaseModel, ConfigDict

from ..schemas.contracts import (
    AgentDecision,
    AgentName,
    ChaseAction,
    ChasePlan,
    ReliabilityScore,
    ToolName,
)
from ..schemas.contracts import ToolCall as AuditToolCall
from .guardrails import (
    build_decision,
    cap_amount,
    has_link_or_contact,
    prose_numbers_ok,
    wrap_untrusted,
)
from .ids import new_id
from .inputs import CycleDue, MemberDue
from .llm import JsonModel, default_models, run_with_failover
from .prompts.smart_chase import SYSTEM_PROMPT, TEMPLATES, ChaseKind
from .toolkit_adapter import (
    BillingInfo,
    CreateInvoiceParameters,
    GenerateInvoiceQrCodeParameters,
    GuardedExecutor,
    Item,
    PlanMoney,
    PrimaryRecipient,
    SendInvoiceParameters,
    SendInvoiceReminderParameters,
    ToolExecutor,
    request_id,
)

__all__ = [
    "ChaseResult",
    "ChaseStepFailed",
    "GRACE_PERIOD",
    "HIGH_RISK_P_LATE",
    "NUDGE_WINDOW",
    "plan_chase",
    "execute_chase",
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
        or has_link_or_contact(text)
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
                min(member.unpaid_amount, cycle.contribution_amount),
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
            min(member.unpaid_amount, cycle.contribution_amount),
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


class ChaseStepFailed(RuntimeError):  # noqa: N818 - name fixed by the interface
    """A PayPal step of `execute_chase` failed; carries what is needed to retry safely."""

    def __init__(
        self,
        step: str,
        message: str,
        *,
        invoice_id: str | None,
        tool_calls: list[AuditToolCall],
    ) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.invoice_id = invoice_id
        self.tool_calls = tool_calls


class ChaseResult(BaseModel):
    """Outcome of `execute_chase`: PayPal references plus the audit record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    invoice_id: str
    invoice_number: str | None = None
    qr_code_base64: str | None = None
    qr_error: str | None = None
    tool_calls: list[AuditToolCall]
    decision: AgentDecision


def _invoice_params(plan: ChasePlan) -> CreateInvoiceParameters:
    if (
        plan.invoice_amount is None
        or plan.invoice_note is None
        or plan.invoice_number is None
        or not plan.recipient_email
    ):
        raise ValueError("invoice plan is missing amount, note, number or recipient email")
    return CreateInvoiceParameters(
        currency_code="USD",
        invoice_number=plan.invoice_number,
        note=plan.invoice_note,
        primary_recipients=[
            PrimaryRecipient(billing_info=BillingInfo(email_address=plan.recipient_email))
        ],
        items=[
            Item(
                name=f"Gam'eya contribution, cycle {plan.cycle_index}",
                quantity="1",
                unit_amount=PlanMoney(currency_code="USD", value=f"{plan.invoice_amount:.2f}"),
            )
        ],
    )


async def execute_chase(
    plan: ChasePlan,
    *,
    executor: ToolExecutor,
    attempt_invoice_id: str | None = None,
) -> ChaseResult:
    """Run an approved INVOICE or REMINDER plan through the guarded toolkit executor.

    Pass `attempt_invoice_id` (from `ChaseStepFailed.invoice_id`) to retry without creating a
    second invoice. Request ids are keyed by `plan.plan_id`, so a retry reuses them.
    """
    if plan.action not in (ChaseAction.INVOICE, ChaseAction.REMINDER):
        raise ValueError(f"cannot execute a {plan.action.value} plan")
    if not plan.plan_id:
        raise ValueError("plan has no plan_id")
    key = plan.plan_id
    guarded = GuardedExecutor(AgentName.SMART_CHASE, executor)

    async def step(tool: ToolName, params: BaseModel, invoice_id: str | None) -> dict[str, Any]:
        try:
            return await guarded.call(tool, params, request_id=request_id(key, tool.value))
        except Exception as exc:
            raise ChaseStepFailed(
                tool.value,
                type(exc).__name__,
                invoice_id=invoice_id,
                tool_calls=list(guarded.records),
            ) from exc

    qr_code: str | None = None
    qr_error: str | None = None
    if plan.action is ChaseAction.REMINDER:
        if not plan.invoice_id or plan.invoice_note is None:
            raise ValueError("reminder plan is missing invoice_id or note")
        invoice_id = plan.invoice_id
        await step(
            ToolName.SEND_INVOICE_REMINDER,
            SendInvoiceReminderParameters(invoice_id=invoice_id, note=plan.invoice_note),
            invoice_id,
        )
    else:
        params = _invoice_params(plan)
        found = attempt_invoice_id
        if found is None:
            created = await step(ToolName.CREATE_INVOICE, params, None)
            new_id_value = created.get("id")
            if not isinstance(new_id_value, str) or not new_id_value:
                raise ChaseStepFailed(
                    ToolName.CREATE_INVOICE.value,
                    "PayPal returned no id",
                    invoice_id=None,
                    tool_calls=list(guarded.records),
                )
            found = new_id_value
        invoice_id = found
        await step(ToolName.SEND_INVOICE, SendInvoiceParameters(invoice_id=invoice_id), invoice_id)
        try:
            qr = await guarded.call(
                ToolName.GENERATE_INVOICE_QR_CODE,
                GenerateInvoiceQrCodeParameters(invoice_id=invoice_id),
                request_id=request_id(key, ToolName.GENERATE_INVOICE_QR_CODE.value),
            )
        except Exception as exc:  # noqa: BLE001 - the QR is a nicety; the invoice is already sent
            qr_error = type(exc).__name__
        else:
            value = qr.get("qr_code_base64")
            if isinstance(value, str) and value:
                qr_code = value
            else:
                qr_error = "empty QR response"

    prior = plan.agent_decision
    return ChaseResult(
        plan_id=key,
        invoice_id=invoice_id,
        invoice_number=plan.invoice_number,
        qr_code_base64=qr_code,
        qr_error=qr_error,
        tool_calls=list(guarded.records),
        decision=build_decision(
            AgentName.SMART_CHASE,
            circle_id=prior.circle_id if prior else None,
            cycle_index=plan.cycle_index,
            model=prior.model if prior else DETERMINISTIC,
            rationale=(
                f"Executed approved {plan.action.value} for invoice {invoice_id} "
                "through the guarded toolkit."
            ),
            tool_calls=guarded.records,
        ),
    )
