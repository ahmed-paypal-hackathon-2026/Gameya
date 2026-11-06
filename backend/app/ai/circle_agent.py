"""Circle Agent: slot-filling chat that ends in a confirmed `CircleSpec` (plan §13.3).

AI understands; deterministic code acts. The agent extracts the spec from the chat,
shows it for confirmation, and only after the organiser confirms does it create the
PayPal product and billing plan, through the guarded toolkit executor and with
parameters the engine builds itself. No DB session and no PayPal access except the
injected `ToolExecutor`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

from ..schemas.contracts import ChatMessage, ChatRole

__all__ = ["CircleSpecDraft", "extract_draft_regex", "missing_fields"]

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
