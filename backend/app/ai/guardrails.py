"""Guardrails between model output and money (plan §13.2).

Tool allow-lists, untrusted-text wrapping, the check on numbers in model prose,
the amount cap and decision records. Pure functions: no I/O, no DB, no PayPal.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from ..schemas.contracts import AgentDecision, AgentName, CircleId, ToolName
from ..schemas.contracts import ToolCall as AuditToolCall
from ._llm_port import UNTRUSTED_TAG, wrap_untrusted
from .ids import new_id

__all__ = [
    "ALLOW_LISTS",
    "UNTRUSTED_TAG",
    "ToolNotAllowed",
    "build_decision",
    "cap_amount",
    "check_tool_allowed",
    "has_link_or_contact",
    "normalise_digits",
    "prose_numbers_ok",
    "wrap_untrusted",
]

RATIONALE_MAX_CHARS = 500

# Every agent has an entry; agents without tools get an empty set. No Payouts
# tool is ever declared (the Agent Toolkit has none).
ALLOW_LISTS: Mapping[AgentName, frozenset[ToolName]] = {
    AgentName.CIRCLE_AGENT: frozenset(
        {
            ToolName.CREATE_PRODUCT,
            ToolName.CREATE_SUBSCRIPTION_PLAN,
            ToolName.SHOW_SUBSCRIPTION_PLAN_DETAILS,
            ToolName.LIST_SUBSCRIPTION_PLANS,
        }
    ),
    AgentName.SMART_CHASE: frozenset(
        {
            ToolName.CREATE_INVOICE,
            ToolName.SEND_INVOICE,
            ToolName.SEND_INVOICE_REMINDER,
            ToolName.GENERATE_INVOICE_QR_CODE,
        }
    ),
    AgentName.MEDIATION: frozenset(),
    AgentName.SWAP_BROKER: frozenset(),
    AgentName.COPILOT: frozenset(),
    AgentName.NEEDS_EXTRACTOR: frozenset(),
    AgentName.FAIR_ORDER: frozenset(),
    AgentName.STRESS_TEST: frozenset(),
    AgentName.LEASH: frozenset(),
}


class ToolNotAllowed(PermissionError):  # noqa: N818 - name fixed by the interface
    """An agent asked for a tool outside its allow-list."""


def check_tool_allowed(agent: AgentName, tool: ToolName | str) -> None:
    """Raise `ToolNotAllowed` unless `tool` is on `agent`'s allow-list."""
    name = tool.value if isinstance(tool, ToolName) else tool
    if name not in {t.value for t in ALLOW_LISTS.get(agent, frozenset())}:
        raise ToolNotAllowed(f"agent {agent.value!r} may not call tool {name!r}")


_DIGIT_TABLE = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬",
    "01234567890123456789.,",
)


def normalise_digits(text: str) -> str:
    """Map Arabic-Indic and Eastern Arabic-Indic digits to 0-9, and the Arabic
    decimal (U+066B) and thousands (U+066C) separators to `.` and `,`."""
    return text.translate(_DIGIT_TABLE)


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _canonical(token: str) -> str:
    """Canonical form of a number token: thousands commas dropped, a comma
    decimal read as a point, and a `.00` decimal equal to its integer."""
    token = normalise_digits(token).strip()
    if _THOUSANDS_RE.match(token):
        token = token.replace(",", "")
    else:
        token = token.replace(",", ".")
    value = Decimal(token)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def prose_numbers_ok(text: str, allowed: Iterable[str]) -> bool:
    """True only if every number in `text` is one the engine supplied."""
    allowed_set = {_canonical(a) for a in allowed}
    return all(_canonical(t) in allowed_set for t in _NUMBER_RE.findall(normalise_digits(text)))


# Common TLDs that make a bare `label.tld` a link even with no path. Any other
# `label.tld` shape counts only when a `/` follows, so "Thanks.Bye" and "e.g." pass.
_TLDS = (
    "com|net|org|edu|gov|info|biz|io|co|me|ly|app|dev|xyz|top|site|online|shop|store|club|live"
    "|link|click|help|support|pay|page|cc|tk|ml|ga|cf|gq|gl|gg|to|ru|cn|us|uk|eg|ae|sa|in|example"
)
_LINK_OR_CONTACT_RE = re.compile(
    r"http|www\.|@"
    rf"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/",
    re.IGNORECASE,
)


def has_link_or_contact(text: str) -> bool:
    """True if model text carries a URL, a scheme-less domain or an email address
    (a phishing vector). Full-width forms are folded with NFKC first."""
    return _LINK_OR_CONTACT_RE.search(unicodedata.normalize("NFKC", text)) is not None


_CENT = Decimal("0.01")


def cap_amount(amount: Decimal, cap: Decimal) -> Decimal:
    """Return `amount` quantised to 0.01; raise `ValueError` if it is not
    positive or exceeds `cap`."""
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be positive")
    if amount > cap:
        raise ValueError(f"amount {amount} exceeds cap {cap}")
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def build_decision(
    agent: AgentName,
    *,
    circle_id: str | None,
    model: str,
    rationale: str,
    cycle_index: int | None = None,
    tool_calls: Sequence[AuditToolCall] = (),
    now: datetime | None = None,
) -> AgentDecision:
    """The audit record for one AI decision (persisted by Person 1)."""
    return AgentDecision(
        decision_id=new_id("agd"),
        agent=agent,
        circle_id=CircleId(circle_id) if circle_id is not None else None,
        cycle_index=cycle_index,
        model=model,
        tool_calls=list(tool_calls),
        rationale=rationale[:RATIONALE_MAX_CHARS],
        created_at=now or datetime.now(UTC),
    )
