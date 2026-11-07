"""Engine-supplied inputs for the AI agents that the shared contracts do not carry.

Smart Chase and Mediation take these instead of reading the ledger themselves. Every amount
comes from the engine; ``Money`` rejects floats and quantises to 0.01 (ROUND_HALF_UP).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from ..schemas.contracts import CircleId, Locale, MemberId, Money, StrictModel


class _FrozenStrict(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class MemberDue(_FrozenStrict):
    """One member's outstanding contribution for the current cycle."""

    member_id: MemberId
    name: str
    email: str
    locale: Locale = "en"
    slot_index: int = Field(ge=0)
    has_received_pot: bool
    contribution_id: str
    unpaid_amount: Money
    open_invoice_id: str | None = None
    invoice_attempts: int = Field(default=0, ge=0)
    reminders_sent: int = Field(default=0, ge=0)


class CycleDue(_FrozenStrict):
    """The cycle the dues belong to."""

    cycle_index: int = Field(ge=0)
    circle_id: CircleId
    circle_name: str
    member_count: int = Field(ge=2)
    due_at: datetime
    contribution_amount: Money
    currency: Literal["USD"] = "USD"
    reserve_balance: Money
