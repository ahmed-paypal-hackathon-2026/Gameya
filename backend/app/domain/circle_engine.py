"""Closing a cycle: ledger invariants first, then exactly one payout (plan §7.3, Task 8).

Golden rule: AI understands and advises; deterministic code moves the money. Nothing
here takes input from a model. The invariants are checked before any HTTP call:

1. the pot is complete: settled (PAID or COVERED) contributions == amount x size_n;
2. one payout per cycle: a payout PayPal accepted is returned, never sent again;
3. the recipient is the approved slot owner and has never been paid in this circle
   (checked against the recipient stored on each payout, so rewriting the order later
   cannot hide an earlier payout);
4. the recipient has an email address.

The payout row is written before PayPal is called, with a deterministic
`sender_batch_id` (also the PayPal-Request-Id), so a crash and retry cannot pay twice.
The id includes a stamp of the circle row, so a demo reset gets fresh ids instead of
PayPal replaying the previous run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Circle, Contribution, Cycle, Membership, Payout
from ..services.paypal.payouts import PayoutError, PayoutResult, send_payout
from .collection import PAYOUT_ACCEPTED, circle_stamp

CENT = Decimal("0.01")
SETTLED = ("PAID", "COVERED")
CREATED = "CREATED"  # row written, PayPal not answered yet
FAILED = "FAILED"  # PayPal refused or did not answer; safe to retry with the same id
BLOCKED = "BLOCKED"  # Payouts not enabled on the sandbox app (contingency C1)
# PayPal accepted the batch: never send it again.
ACCEPTED = PAYOUT_ACCEPTED


class InvariantViolation(ValueError):  # noqa: N818 - name fixed by the plan interface
    """A ledger invariant failed; no money moved."""


class PayoutSender(Protocol):
    async def __call__(
        self,
        *,
        sender_batch_id: str,
        receiver_email: str,
        amount: Decimal,
        token_provider: Callable[[], Awaitable[str]],
    ) -> PayoutResult: ...


def sender_batch_id(circle: Circle, cycle_index: int) -> str:
    return f"gameya-{circle.id}-{cycle_index}-{circle_stamp(circle)}"


def is_accepted(payout: Payout) -> bool:
    return bool(payout.paypal_batch_id) or payout.status in ACCEPTED


def _load(db: Session, cycle_id: str) -> tuple[Cycle, Circle]:
    cycle = db.get(Cycle, cycle_id)
    circle = db.get(Circle, cycle.circle_id) if cycle is not None else None
    if cycle is None or circle is None:
        raise LookupError(f"unknown cycle {cycle_id}")
    return cycle, circle


def _check_invariants(db: Session, cycle: Cycle, circle: Circle) -> tuple[Decimal, Membership]:
    pot = (circle.contribution_amount * circle.size_n).quantize(CENT)
    settled = db.scalars(
        select(Contribution).where(
            Contribution.cycle_id == cycle.id, Contribution.status.in_(SETTLED)
        )
    ).all()
    collected = sum((c.amount for c in settled), Decimal("0")).quantize(CENT)
    if collected != pot:
        raise InvariantViolation(f"the pot is incomplete: {collected} of {pot} USD collected")

    if not cycle.payout_member_id:
        raise InvariantViolation("no recipient: the payout order has not been approved")
    recipient = db.scalars(
        select(Membership).where(
            Membership.circle_id == circle.id, Membership.member_id == cycle.payout_member_id
        )
    ).first()
    if recipient is None:
        raise InvariantViolation("the recipient is not a member of this circle")

    earlier = db.scalars(
        select(Payout)
        .join(Cycle, Cycle.id == Payout.cycle_id)
        .where(
            Cycle.circle_id == circle.id,
            Cycle.id != cycle.id,
            Payout.recipient_member_id == recipient.member_id,
        )
    ).all()
    if any(is_accepted(p) for p in earlier):
        raise InvariantViolation(f"{recipient.member_id} was already paid in this circle")
    if not recipient.email:
        raise InvariantViolation("the recipient has no email address")
    return pot, recipient


async def close_cycle(
    db: Session,
    cycle_id: str,
    *,
    payouts: PayoutSender = send_payout,
    token_provider: Callable[[], Awaitable[str]],
) -> Payout:
    """Pay this cycle's pot to its approved recipient, once. Raises InvariantViolation,
    or PayoutError when PayPal refuses (the row is then FAILED and a retry is safe)."""
    cycle, circle = _load(db, cycle_id)
    existing = db.scalars(select(Payout).where(Payout.cycle_id == cycle.id)).first()
    if existing is not None and is_accepted(existing):
        return existing  # invariant 2: one payout per cycle

    pot, recipient = _check_invariants(db, cycle, circle)
    batch = existing.sender_batch_id if existing else sender_batch_id(circle, cycle.cycle_index)
    payout = existing or Payout(
        id=f"po-{circle.id}-{cycle.cycle_index}"[:64],
        cycle_id=cycle.id,
        amount=pot,
        sender_batch_id=batch,
        paypal_request_id=batch,
        status=CREATED,
    )
    payout.recipient_member_id = recipient.member_id
    if existing is None:
        db.add(payout)
    db.flush()  # recorded before any HTTP call

    if not get_settings().payouts_enabled:
        payout.status = BLOCKED
        db.flush()
        return payout

    try:
        result = await payouts(
            sender_batch_id=batch,
            receiver_email=recipient.email,
            amount=pot,
            token_provider=token_provider,
        )
    except PayoutError:
        payout.status = FAILED
        db.flush()
        raise
    payout.paypal_batch_id = result.batch_id
    payout.status = result.status[:16]
    if result.status in ACCEPTED:
        cycle.status = "PAID_OUT"
        cycle.closed_at = datetime.now(UTC)
        payout.paid_at = datetime.now(UTC) if result.status == "SUCCESS" else None
    db.flush()
    return payout
