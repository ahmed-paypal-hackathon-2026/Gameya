"""Typed, JSON-friendly facts that the pure guard evaluates.

Datetimes are timezone-aware UTC (naive input is treated as UTC), money is a
``Decimal`` quantised to 0.01 (ROUND_HALF_UP, never float) and emails are
stripped and lowercased.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Protocol

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field
from sqlalchemy.orm import Session

from ..schemas.contracts import GuardAction

_CENT = Decimal("0.01")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float is not allowed for money; use str, int or Decimal")  # noqa: TRY004 (pydantic needs ValueError)
    return value


def _quantise(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _norm_email(value: str) -> str:
    return value.strip().lower()


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
Money = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(_quantise)]
Email = Annotated[str, AfterValidator(_norm_email)]


class BaseFacts(BaseModel):
    now: UtcDatetime
    circle_id: str
    cycle_index: int | None = None
    approved_rule_ids: list[str] = Field(default_factory=list)


class PayoutFacts(BaseFacts):
    payout_id: str
    amount: Money
    recipient_member_id: str
    recipient_name: str
    recipient_payout_email: Email
    payout_email_changed_at: UtcDatetime | None = None
    payer_emails: list[Email]
    reserve_cover_used: bool
    slot_swapped_at: UtcDatetime | None = None
    recipient_is_organiser: bool
    recipient_has_unpaid: bool


class ChaseInvoiceFacts(BaseFacts):
    member_id: str
    member_name: str
    invoice_amount: Money
    unpaid_amount: Money
    due_at: UtcDatetime
    open_invoice_count: int
    last_invoice_sent_at: UtcDatetime | None = None
    no_auto_chase: bool


class ReminderFacts(BaseFacts):
    member_id: str
    member_name: str
    last_reminder_sent_at: UtcDatetime | None = None
    reminders_sent_this_cycle: int


class SwapFacts(BaseFacts):
    member_a_name: str
    member_b_name: str
    slot_a_payout_at: UtcDatetime
    slot_b_payout_at: UtcDatetime
    moved_earlier_member_name: str | None = None
    moved_earlier_p_late: Decimal | None = None


FACTS_MODEL: dict[GuardAction, type[BaseFacts]] = {
    GuardAction.RELEASE_POT_PAYOUT: PayoutFacts,
    GuardAction.SEND_CHASE_INVOICE: ChaseInvoiceFacts,
    GuardAction.SEND_INVOICE_REMINDER: ReminderFacts,
    GuardAction.APPLY_SLOT_SWAP: SwapFacts,
}


class FactsProvider(Protocol):
    def build(self, db: Session, action: GuardAction, subject_id: str) -> tuple[str, BaseFacts]:
        """Return ``(subject_type, facts)`` for the subject of a guarded action."""
        ...
