"""Circle Agent: slot-filling chat that ends in a confirmed `CircleSpec` (plan §13.3).

AI understands; deterministic code acts. The agent extracts the spec from the chat,
shows it for confirmation, and only after the organiser confirms does it create the
PayPal product and billing plan, through the guarded toolkit executor and with
parameters the engine builds itself. No DB session and no PayPal access except the
injected `ToolExecutor`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..ml.stress_test import deposit_per_member
from ..schemas.contracts import (
    AgentName,
    ChatMessage,
    ChatRole,
    CircleAgentTurn,
    CircleId,
    CircleSnapshot,
    CircleSpec,
    CycleUnit,
)
from .guardrails import build_decision, wrap_untrusted
from .llm import JsonModel, ToolPlanModel, default_models, run_with_failover
from .prompts.circle_agent import EXTRACT_SYSTEM_PROMPT
from .toolkit_adapter import ToolExecutor

logger = logging.getLogger(__name__)

__all__ = ["CircleSpecDraft", "circle_agent_turn", "extract_draft_regex", "missing_fields"]

LLM_BUDGET_SECONDS = 6.0
LLM_PROVIDER_TIMEOUT_SECONDS = 5.0
MAX_PROMPT_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000
DETERMINISTIC = "deterministic"

_REQUIRED = ("member_count", "contribution_amount", "interval", "start_date")

_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_MONTH_ALT = "|".join(_MONTHS)

_COUNT_RE = re.compile(r"(?<![\d$.])\b(\d+)\s*(?:friends|members|people)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
_MONTH_WORD_RE = re.compile(r"\bmonth(?:ly|s)?\b", re.IGNORECASE)
_WEEK_WORD_RE = re.compile(r"\bweek(?:ly|s)?\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALT})\b", re.IGNORECASE
)
_MONTH_DAY_RE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)


class CircleSpecDraft(BaseModel):
    """What the model (or the regex fallback) found so far. All fields optional.

    `contribution_amount` and `start_date` are strings so Gemini's schema stays simple;
    `CircleSpec` validates them afterwards.
    """

    member_count: int | None = None
    contribution_amount: str | None = None
    interval: Literal["WEEK", "MONTH"] | None = None
    start_date: str | None = None
    name: str | None = None
    purpose: str | None = None


def missing_fields(draft: CircleSpecDraft) -> list[str]:
    """Names of the required slots still empty, in conversation order."""
    return [field for field in _REQUIRED if getattr(draft, field) is None]


def _next_occurrence(day: int, month: int, today: date) -> date | None:
    for year in range(today.year, today.year + 5):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _find_date(text: str, today: date) -> date | None:
    """The latest date mentioned in `text` (ISO, `1 November` or `November 1`)."""
    found: list[tuple[int, date]] = []
    for m in _ISO_RE.finditer(text):
        try:
            found.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            continue
    for m in _DAY_MONTH_RE.finditer(text):
        resolved = _next_occurrence(int(m[1]), _MONTHS[m[2].lower()], today)
        if resolved is not None:
            found.append((m.start(), resolved))
    for m in _MONTH_DAY_RE.finditer(text):
        resolved = _next_occurrence(int(m[2]), _MONTHS[m[1].lower()], today)
        if resolved is not None:
            found.append((m.start(), resolved))
    return max(found, key=lambda pair: pair[0])[1] if found else None


def _find_interval(text: str) -> Literal["WEEK", "MONTH"] | None:
    month = [m.start() for m in _MONTH_WORD_RE.finditer(text)]
    week = [m.start() for m in _WEEK_WORD_RE.finditer(text)]
    if not month and not week:
        return None
    return "MONTH" if max(month, default=-1) > max(week, default=-1) else "WEEK"


def extract_draft_regex(history: list[ChatMessage], today: date) -> CircleSpecDraft:
    """Deterministic extraction from the organiser's (USER) messages only.

    For every field the latest user message that mentions it wins.
    """
    member_count: int | None = None
    amount: str | None = None
    interval: Literal["WEEK", "MONTH"] | None = None
    start: date | None = None
    for message in history:
        if message.role is not ChatRole.USER:
            continue
        text = message.content
        counts = _COUNT_RE.findall(text)
        if counts:
            member_count = int(counts[-1])
        amounts = _AMOUNT_RE.findall(text)
        if amounts:
            amount = amounts[-1]
        interval = _find_interval(text) or interval
        start = _find_date(text, today) or start
    return CircleSpecDraft(
        member_count=member_count,
        contribution_amount=amount,
        interval=interval,
        start_date=start.isoformat() if start else None,
        name=f"{member_count}-member circle" if member_count is not None else None,
    )


_MISSING_PHRASES = {
    "member_count": "how many members",
    "contribution_amount": "the amount per cycle",
    "interval": "how often (weekly or monthly)",
    "start_date": "the start date",
}
_UNIT_PHRASE = {CycleUnit.MONTH: "per month", CycleUnit.WEEK: "per week"}


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _missing_reply(missing: list[str]) -> str:
    return f"Got it. I still need {_join([_MISSING_PHRASES[f] for f in missing])}."


def _build_spec(draft: CircleSpecDraft) -> tuple[CircleSpec | None, str | None]:
    """Validate a complete draft. Returns (spec, None) or (None, reason naming the field)."""
    member_count = draft.member_count
    amount = (draft.contribution_amount or "").lstrip("$ ").replace(",", "")
    try:
        spec = CircleSpec.model_validate(
            {
                "name": (draft.name or "").strip() or f"{member_count}-member circle",
                "member_count": member_count,
                "contribution_amount": amount,
                "cycle_unit": draft.interval,
                "start_date": draft.start_date,
                "purpose": draft.purpose or None,
            }
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        field = str(first["loc"][0]) if first["loc"] else "spec"
        detail = first["msg"]
        return None, f"I can't use that yet: {field} is invalid ({detail}). Please correct it."
    return spec, None


def _snapshot(spec: CircleSpec, reserve_pct: Decimal, circle_id: str | None) -> CircleSnapshot:
    return CircleSnapshot(
        circle_id=CircleId(circle_id or "pending"),
        name=spec.name,
        contribution_amount=spec.contribution_amount,
        currency=spec.currency,
        cycle_unit=spec.cycle_unit,
        size_n=spec.member_count,
        start_date=spec.start_date,
        reserve_pct=reserve_pct,
    )


def _confirm_card(spec: CircleSpec, reserve_pct: Decimal, circle_id: str | None) -> str:
    deposit = ""
    if reserve_pct > 0:
        amount = deposit_per_member(_snapshot(spec, reserve_pct, circle_id), reserve_pct)
        deposit = f", ${amount:.2f} upfront reserve deposit"
    return (
        f"{spec.name}: {spec.member_count} members, ${spec.contribution_amount:.2f} "
        f"{_UNIT_PHRASE[spec.cycle_unit]}, starting {spec.start_date.isoformat()}, "
        f"{spec.member_count} cycles{deposit}. Confirm to create it on PayPal."
    )


def _history_prompt(history: list[ChatMessage], today: date) -> str:
    lines = [
        f"{m.role.value}: {wrap_untrusted(m.content, max_chars=MAX_MESSAGE_CHARS)}"
        for m in history[-MAX_PROMPT_MESSAGES:]
    ]
    header = f"Today: {today.isoformat()}\nConversation:\n"
    return header + "\n".join(lines)


async def _extract_with_llm(
    history: list[ChatMessage], today: date, models: Sequence[JsonModel]
) -> tuple[CircleSpecDraft, str] | None:
    """One LLM call over the whole history; None when it fails or finds nothing."""
    answered: list[str] = []
    prompt = _history_prompt(history, today)

    async def attempt(model: JsonModel, _timeout: float) -> CircleSpecDraft:
        draft = await model.generate_json(EXTRACT_SYSTEM_PROMPT, prompt, CircleSpecDraft)
        answered.append(model.name)
        return draft

    draft = await run_with_failover(
        models,
        attempt,
        purpose="circle_agent.extract",
        budget_seconds=LLM_BUDGET_SECONDS,
        provider_timeout=LLM_PROVIDER_TIMEOUT_SECONDS,
    )
    if draft is None or draft == CircleSpecDraft():
        return None
    return draft, answered[-1]


async def circle_agent_turn(
    history: list[ChatMessage],
    confirmed: bool,
    *,
    confirmed_spec: CircleSpec | None = None,
    executor: ToolExecutor | None = None,
    today: date | None = None,
    json_models: Sequence[JsonModel] | None = None,
    plan_models: Sequence[ToolPlanModel] | None = None,
    reserve_pct: Decimal = Decimal("0"),
    circle_id: CircleId | None = None,
) -> CircleAgentTurn:
    """One organiser turn: fill the spec (no PayPal call) or, once confirmed, create the plan."""
    if confirmed:
        if confirmed_spec is None or executor is None:
            raise ValueError("a confirmed turn needs confirmed_spec and executor")
        raise NotImplementedError
    today = today or date.today()
    models = json_models if json_models is not None else default_models()
    draft: CircleSpecDraft | None = None
    source = DETERMINISTIC
    found = await _extract_with_llm(history, today, models) if models else None
    if found is not None:
        draft, source = found
    if draft is None:
        draft = extract_draft_regex(history, today)

    missing = missing_fields(draft)
    spec: CircleSpec | None = None
    if missing:
        reply = _missing_reply(missing)
        rationale = f"Still missing: {', '.join(missing)}."
    else:
        spec, problem = _build_spec(draft)
        if spec is None:
            reply = problem or "I can't use that yet."
            rationale = "Draft failed validation."
        else:
            reply = _confirm_card(spec, reserve_pct, circle_id)
            rationale = "Spec complete; waiting for organiser confirmation. No PayPal call made."
    return CircleAgentTurn(
        reply=reply,
        spec=spec,
        needs_confirmation=spec is not None,
        agent_decision=build_decision(
            AgentName.CIRCLE_AGENT,
            circle_id=circle_id,
            model=source,
            rationale=rationale,
        ),
    )
