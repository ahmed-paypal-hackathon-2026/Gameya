"""Collecting a cycle's contributions with real PayPal invoices (plan Task 7).

Deterministic engine code: the amounts come from the circle row, the invoice text is a
fixed template, and only the engine (never an agent) creates these invoices or reads
their status. Smart Chase follows up on the members who still owe.

- `open_collection`: one invoice per active member for the contribution amount.
  Re-running it skips members who already have one, and the PayPal request ids are
  derived from (cycle, member), so a retry cannot double-invoice.
- `sync_collection`: reads each open invoice's status; PAID becomes a PAID contribution
  carrying the PayPal invoice id. When everyone has paid, the cycle is READY.
- `chase_cycle`: Smart Chase plans per unpaid member and executes the plans that touch
  PayPal (an invoice or a reminder), within the contribution cap.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from ulid import ULID

from ..ai.guardrails import safe_display_text
from ..ai.inputs import CycleDue, MemberDue
from ..ai.smart_chase import ChaseStepFailed, execute_chase, plan_chase
from ..ai.toolkit_adapter import (
    BillingInfo,
    CreateInvoiceParameters,
    Item,
    PlanMoney,
    PrimaryRecipient,
    ToolExecutor,
)
from ..db.models import Circle, Contribution, Cycle, Invoice, Membership, Payout
from ..schemas.contracts import ChaseAction, CircleId, MemberId
from ..services.circle_data import (
    active_members,
    current_proposal,
    record_decision,
    reliability_scores,
)
from ..services.paypal.invoicing import PAID_STATUSES, InvoiceGateway

CENT = Decimal("0.01")
SETTLED = frozenset({"PAID", "COVERED"})
# PayPal accepted the payout batch (one definition, shared with circle_engine).
PAYOUT_ACCEPTED = frozenset({"PENDING", "PROCESSING", "SUCCESS", "ALREADY_SENT"})


UNSENT = "UNSENT"  # PayPal created the invoice but sending it failed


class CycleNotFound(LookupError):
    pass


class CycleNotReady(ValueError):  # noqa: N818 - reads as a state, like CycleNotFound
    """Collecting now would bill members for a pot that cannot be paid out."""


def circle_stamp(circle: Circle) -> str:
    """Short stamp of this circle row. A demo reset recreates the row, so ids built with
    it (PayPal request ids, sender_batch_id) never repeat a previous run's ids."""
    created = circle.created_at.isoformat() if circle.created_at else ""
    return hashlib.sha256(f"{circle.id}|{created}".encode()).hexdigest()[:8]


def require_ready(db: Session, cycle: Cycle, circle: Circle) -> None:
    """Members are billed only when the pot has a recipient and every seat is filled."""
    if not cycle.payout_member_id:
        raise CycleNotReady("approve the payout order before collecting")
    if len(active_members(db, circle.id)) != circle.size_n:
        raise CycleNotReady("fill every seat before collecting")
    # Members see the current proposal; the money follows the applied order. They must agree,
    # or a need changed after approval and members would be paid in an order they never saw.
    shown = current_proposal(db, circle.id)
    if shown is not None:
        applied = {
            c.cycle_index: c.payout_member_id
            for c in db.scalars(select(Cycle).where(Cycle.circle_id == circle.id)).all()
        }
        if {a.slot_index: a.member_id for a in shown.assignments} != applied:
            raise CycleNotReady(
                "the payout order changed: every member must approve the new order "
                "before collecting"
            )


@dataclass(frozen=True)
class ChaseOutcome:
    member_id: str
    action: str
    invoice_id: str | None
    error: str | None = None


def _cycle_and_circle(db: Session, cycle_id: str) -> tuple[Cycle, Circle]:
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise CycleNotFound(cycle_id)
    circle = db.get(Circle, cycle.circle_id)
    if circle is None:
        raise CycleNotFound(cycle_id)
    return cycle, circle


def circle_id_of_cycle(db: Session, cycle_id: str) -> str:
    return _cycle_and_circle(db, cycle_id)[1].id


def invoice_number(circle: Circle, cycle_index: int, member_id: str) -> str:
    """Unique per circle row, cycle and member, and within PayPal's 25 characters.

    The circle's creation time is part of the hash, so a demo reset (which recreates
    the circle) never reuses a number PayPal has already seen.
    """
    created = circle.created_at.isoformat() if circle.created_at else ""
    digest = hashlib.sha256(f"{circle.id}|{created}|{member_id}".encode()).hexdigest()[:10]
    return f"GMY-{cycle_index + 1}-{digest.upper()}"


def _note(circle: Circle, cycle: Cycle, amount: Decimal) -> str:
    name = safe_display_text(circle.name, "your circle")
    return (
        f"{name}: your contribution for cycle {cycle.cycle_index + 1} is {amount:.2f} USD, "
        f"due {cycle.due_date.isoformat()}. Pay this invoice to keep the pot complete."
    )


def _invoice_params(
    circle: Circle, cycle: Cycle, member: Membership, amount: Decimal
) -> CreateInvoiceParameters:
    return CreateInvoiceParameters(
        currency_code="USD",
        invoice_number=invoice_number(circle, cycle.cycle_index, member.member_id),
        note=_note(circle, cycle, amount),
        primary_recipients=[PrimaryRecipient(billing_info=BillingInfo(email_address=member.email))],
        items=[
            Item(
                name=f"Gam'eya contribution, cycle {cycle.cycle_index + 1}",
                quantity="1",
                unit_amount=PlanMoney(currency_code="USD", value=f"{amount:.2f}"),
            )
        ],
    )


def _contribution(db: Session, cycle: Cycle, member_id: str, amount: Decimal) -> Contribution:
    """The member's contribution row for this cycle, created PENDING if missing."""
    row = db.scalars(
        select(Contribution).where(
            Contribution.cycle_id == cycle.id, Contribution.member_id == member_id
        )
    ).first()
    if row is None:
        row = Contribution(
            id=f"ct-{ULID()}",
            cycle_id=cycle.id,
            member_id=member_id,
            kind="INVOICE",
            status="PENDING",
            amount=amount,
        )
        db.add(row)
        db.flush()
    return row


def _invoices(db: Session, cycle_id: str) -> list[Invoice]:
    return list(
        db.scalars(
            select(Invoice).where(Invoice.cycle_id == cycle_id).order_by(Invoice.created_at)
        ).all()
    )


async def open_collection(db: Session, cycle_id: str, gateway: InvoiceGateway) -> list[Invoice]:
    """Invoice every active member once for this cycle; returns the cycle's invoices.

    Each sent invoice is committed at once, so a PayPal failure part-way through keeps
    the invoices already sent and a retry skips them.
    """
    cycle, circle = _cycle_and_circle(db, cycle_id)
    require_ready(db, cycle, circle)
    amount = circle.contribution_amount.quantize(CENT)
    stamp = circle_stamp(circle)
    invoiced = {i.member_id for i in _invoices(db, cycle_id) if i.paypal_invoice_id}
    for member in active_members(db, circle.id):
        if member.member_id in invoiced:
            continue
        contribution = _contribution(db, cycle, member.member_id, amount)
        if contribution.status in SETTLED:
            continue
        params = _invoice_params(circle, cycle, member, amount)
        paypal_id = await gateway.create_and_send(
            params, key=f"collect:{cycle.id}:{stamp}:{member.member_id}"
        )
        db.add(
            Invoice(
                id=f"inv-{ULID()}",
                circle_id=circle.id,
                cycle_id=cycle.id,
                member_id=member.member_id,
                amount=amount,
                currency="USD",
                paypal_invoice_id=paypal_id,
                status="SENT",
                note=params.note,
                sent_at=datetime.now(UTC),
            )
        )
        contribution.status = "INVOICED"
        if cycle.status == "UPCOMING":
            cycle.status = "DUE"
        db.commit()
    db.flush()
    return _invoices(db, cycle_id)


def unpaid_member_ids(db: Session, cycle_id: str) -> list[str]:
    cycle, circle = _cycle_and_circle(db, cycle_id)
    paid = {
        c.member_id
        for c in db.scalars(select(Contribution).where(Contribution.cycle_id == cycle.id)).all()
        if c.status in SETTLED
    }
    return [m.member_id for m in active_members(db, circle.id) if m.member_id not in paid]


async def sync_collection(db: Session, cycle_id: str, gateway: InvoiceGateway) -> int:
    """Pull invoice statuses from PayPal; returns how many contributions became PAID."""
    cycle, circle = _cycle_and_circle(db, cycle_id)
    newly_paid = 0
    for invoice in _invoices(db, cycle_id):
        # An UNSENT draft keeps its marker: Smart Chase resends it, so it is never re-created.
        if not invoice.paypal_invoice_id or invoice.status == UNSENT:
            continue
        contribution = _contribution(
            db, cycle, invoice.member_id, circle.contribution_amount.quantize(CENT)
        )
        if contribution.status in SETTLED:
            continue
        status = await gateway.get_status(invoice.paypal_invoice_id)
        invoice.status = status[:16]
        if status in PAID_STATUSES:
            contribution.status = "PAID"
            contribution.paypal_ref = invoice.paypal_invoice_id
            contribution.updated_at = datetime.now(UTC)
            newly_paid += 1
    if not unpaid_member_ids(db, cycle_id) and cycle.status in ("UPCOMING", "DUE", "GRACE"):
        cycle.status = "READY"
    db.flush()
    return newly_paid


def _has_received_pot(db: Session, circle_id: str, member_id: str) -> bool:
    cycle_ids = select(Cycle.id).where(Cycle.circle_id == circle_id)
    payouts = db.scalars(
        select(Payout).where(
            Payout.cycle_id.in_(cycle_ids), Payout.recipient_member_id == member_id
        )
    ).all()
    return any(p.paypal_batch_id or p.status in PAYOUT_ACCEPTED for p in payouts)


async def chase_cycle(
    db: Session, cycle_id: str, executor: ToolExecutor, *, now: datetime, actor: str
) -> list[ChaseOutcome]:
    """Smart Chase for every unpaid member; PayPal actions only after the grace period.

    Each member is committed on its own, and an invoice PayPal created is stored even if
    sending it failed, so a later chase resends that invoice instead of creating another.
    """
    cycle, circle = _cycle_and_circle(db, cycle_id)
    require_ready(db, cycle, circle)
    amount = circle.contribution_amount.quantize(CENT)
    unpaid = set(unpaid_member_ids(db, cycle_id))
    scores = reliability_scores(db, circle.id)
    due = CycleDue(
        cycle_index=cycle.cycle_index,
        circle_id=CircleId(circle.id),
        circle_name=circle.name,
        member_count=circle.size_n,
        due_at=datetime.combine(cycle.due_date, time(0, 0), tzinfo=UTC),
        contribution_amount=amount,
        reserve_balance=Decimal("0.00"),
    )
    all_invoices = [i for i in _invoices(db, cycle_id) if i.paypal_invoice_id]
    sent = {i.member_id: i for i in all_invoices if i.status != UNSENT}
    unsent = {i.member_id: i for i in all_invoices if i.status == UNSENT}
    outcomes: list[ChaseOutcome] = []
    for member in active_members(db, circle.id):
        if member.member_id not in unpaid:
            continue
        try:
            outcome = await _chase_member(
                db,
                cycle,
                circle,
                member,
                amount,
                due,
                scores,
                executor,
                invoice=sent.get(member.member_id),
                draft=unsent.get(member.member_id),
                now=now,
                actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 - one member's failure must not undo the others
            db.rollback()
            outcome = ChaseOutcome(member.member_id, "error", None, type(exc).__name__)
        else:
            db.commit()
        outcomes.append(outcome)
    return outcomes


async def _chase_member(
    db: Session,
    cycle: Cycle,
    circle: Circle,
    member: Membership,
    amount: Decimal,
    due: CycleDue,
    scores: dict[MemberId, Any],
    executor: ToolExecutor,
    *,
    invoice: Invoice | None,
    draft: Invoice | None,
    now: datetime,
    actor: str,
) -> ChaseOutcome:
    contribution = _contribution(db, cycle, member.member_id, amount)
    member_due = MemberDue(
        member_id=MemberId(member.member_id),
        name=member.name,
        email=member.email,
        locale="ar-EG" if member.locale == "ar-EG" else "en",
        slot_index=member.slot_index if member.slot_index is not None else 0,
        has_received_pot=_has_received_pot(db, circle.id, member.member_id),
        contribution_id=contribution.id,
        unpaid_amount=amount,
        open_invoice_id=invoice.paypal_invoice_id if invoice else None,
        invoice_attempts=1 if (invoice or draft) else 0,
        reminders_sent=invoice.reminders_sent if invoice else 0,
    )
    plan = await plan_chase(member_due, due, scores[MemberId(member.member_id)], now=now)
    if plan.agent_decision is not None:
        record_decision(db, plan.agent_decision, actor)
    if plan.action not in (ChaseAction.INVOICE, ChaseAction.REMINDER):
        return ChaseOutcome(member.member_id, plan.action.value, None)
    try:
        result = await execute_chase(
            plan,
            executor=executor,
            contribution_amount=amount,
            # An invoice PayPal already created is sent again, never created twice.
            attempt_invoice_id=draft.paypal_invoice_id if draft else None,
        )
    except ChaseStepFailed as exc:
        if exc.invoice_id and draft is None and plan.action is ChaseAction.INVOICE:
            _store_invoice(db, cycle, circle, member, plan, amount, exc.invoice_id, UNSENT)
        return ChaseOutcome(member.member_id, plan.action.value, exc.invoice_id, exc.step)
    record_decision(db, result.decision, actor)
    if plan.action is ChaseAction.REMINDER and invoice is not None:
        invoice.reminders_sent = (invoice.reminders_sent or 0) + 1
    elif draft is not None:
        draft.status, draft.sent_at = "SENT", datetime.now(UTC)
    else:
        _store_invoice(db, cycle, circle, member, plan, amount, result.invoice_id, "SENT")
    contribution.status = "INVOICED"
    db.flush()
    return ChaseOutcome(member.member_id, plan.action.value, result.invoice_id)


def _store_invoice(
    db: Session,
    cycle: Cycle,
    circle: Circle,
    member: Membership,
    plan: Any,
    amount: Decimal,
    paypal_id: str,
    status: str,
) -> None:
    db.add(
        Invoice(
            id=f"inv-{ULID()}",
            circle_id=circle.id,
            cycle_id=cycle.id,
            member_id=member.member_id,
            amount=plan.invoice_amount or amount,
            currency="USD",
            paypal_invoice_id=paypal_id,
            status=status,
            note=plan.invoice_note,
            sent_at=datetime.now(UTC) if status == "SENT" else None,
        )
    )
    db.flush()
