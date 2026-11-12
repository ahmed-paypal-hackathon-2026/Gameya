"""SQLAlchemy 2 tables for the Gam'eya ledger (plans §7.3).

Money columns are NUMERIC(12,2); Decimal quantised to 0.01 ROUND_HALF_UP on the
Python side by schemas/contracts.Money. Floats are banned on financial paths.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Circle(Base):
    __tablename__ = "circles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    contribution_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    cycle_unit: Mapped[str] = mapped_column(String(16), default="MONTH")
    size_n: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    reserve_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    organiser_member_id: Mapped[str | None] = mapped_column(String(64))
    paypal_product_id: Mapped[str | None] = mapped_column(String(64))
    paypal_plan_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="PLANNED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("circle_id", "member_id", name="uq_member_per_circle"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"))
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(12), default="en")
    email: Mapped[str] = mapped_column(String(200))
    paypal_payer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    is_organiser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    slot_index: Mapped[int | None] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (UniqueConstraint("circle_id", "cycle_index", name="uq_cycle_index"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"))
    cycle_index: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="UPCOMING")
    payout_member_id: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Contribution(Base):
    __tablename__ = "contributions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("cycles.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="SUBSCRIPTION")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paypal_ref: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    covered_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Payout(Base):
    __tablename__ = "payouts"
    __table_args__ = (UniqueConstraint("cycle_id", name="uq_one_payout_per_cycle"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("cycles.id"))
    # Who was paid, fixed when the payout is created: a later order change cannot hide it.
    recipient_member_id: Mapped[str | None] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    sender_batch_id: Mapped[str] = mapped_column(String(80), index=True, unique=True)
    paypal_request_id: Mapped[str] = mapped_column(String(80))
    paypal_batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("cycles.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    paypal_invoice_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    note: Mapped[str | None] = mapped_column(Text)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str | None] = mapped_column(String(64), index=True)
    agent: Mapped[str] = mapped_column(String(48))
    decision: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    tools_json: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paypal_event_id: Mapped[str] = mapped_column(String(80), index=True, unique=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NeedRecord(Base):
    """A member's latest extracted need (plan Task 6). The raw text is not kept."""

    __tablename__ = "needs"
    __table_args__ = (UniqueConstraint("circle_id", "member_id", name="uq_need_per_member"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(64))
    need_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # "llm" or "rules"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OrderProposalRecord(Base):
    """The circle's current payout-order proposal; approvals are tied to its hash."""

    __tablename__ = "order_proposals"

    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"), primary_key=True)
    proposal_hash: Mapped[str] = mapped_column(String(64))
    proposal_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OrderApproval(Base):
    """One member's approval of one proposal (unique, so approving twice is a no-op)."""

    __tablename__ = "order_approvals"
    __table_args__ = (
        UniqueConstraint("circle_id", "member_id", "proposal_hash", name="uq_order_approval"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(64))
    proposal_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
