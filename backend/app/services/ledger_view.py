"""The organiser's ledger and audit grids, read from the database (plan Task 9).

Every PayPal reference shown is a real id from a stored row: the invoice id on a paid
contribution, the payout batch id (or our sender_batch_id until PayPal answers).
"""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AgentDecision, Contribution, Cycle, Invoice, Membership, Payout
from ..schemas.contracts import (
    AgentDecision as AgentDecisionContract,
)
from ..schemas.contracts import (
    AgentName,
    AuditResponse,
    CircleId,
    ContributionSource,
    ContributionStatus,
    LedgerResponse,
    LedgerRow,
    MemberId,
    PayoutRow,
    PayoutStatus,
    ToolCall,
)

CENT = Decimal("0.01")
_PAYOUT_STATUS = {
    "SUCCESS": PayoutStatus.PAID_OUT,
    "ALREADY_SENT": PayoutStatus.PAID_OUT,
    "PENDING": PayoutStatus.PROCESSING,
    "PROCESSING": PayoutStatus.PROCESSING,
    "BLOCKED": PayoutStatus.PENDING,
    "CREATED": PayoutStatus.PENDING,
    "UNCLAIMED": PayoutStatus.UNCLAIMED,
}
# Counted as paid out only once PayPal accepted the batch.
_ACCEPTED = frozenset({"SUCCESS", "ALREADY_SENT", "PENDING", "PROCESSING", "UNCLAIMED"})


def _status(value: str) -> ContributionStatus:
    try:
        return ContributionStatus(value.lower())
    except ValueError:
        return ContributionStatus.PENDING


def _source(kind: str) -> ContributionSource:
    try:
        return ContributionSource(kind.lower())
    except ValueError:
        return ContributionSource.INVOICE


def ledger(db: Session, circle_id: str) -> LedgerResponse:
    cycles = {c.id: c for c in db.scalars(select(Cycle).where(Cycle.circle_id == circle_id))}
    names = {
        m.member_id: m.name
        for m in db.scalars(select(Membership).where(Membership.circle_id == circle_id))
    }
    invoice_of = {
        (i.cycle_id, i.member_id): i.paypal_invoice_id
        for i in db.scalars(select(Invoice).where(Invoice.circle_id == circle_id))
        if i.paypal_invoice_id
    }
    contributions = db.scalars(
        select(Contribution)
        .where(Contribution.cycle_id.in_(list(cycles)))
        .order_by(Contribution.cycle_id, Contribution.member_id)
    ).all()
    rows = [
        LedgerRow(
            row_id=c.id,
            cycle_index=cycles[c.cycle_id].cycle_index,
            member_id=MemberId(c.member_id),
            member_name=names.get(c.member_id, c.member_id),
            amount=c.amount.quantize(CENT),
            status=_status(c.status),
            source=_source(c.kind),
            paypal_reference=c.paypal_ref or invoice_of.get((c.cycle_id, c.member_id)),
            created_at=c.updated_at,
        )
        for c in contributions
        if c.amount > 0
    ]
    rows.sort(key=lambda r: (r.cycle_index, r.member_name))
    payouts = db.scalars(
        select(Payout).where(Payout.cycle_id.in_(list(cycles))).order_by(Payout.created_at)
    ).all()
    payout_rows = [
        PayoutRow(
            payout_id=p.id,
            cycle_index=cycles[p.cycle_id].cycle_index,
            recipient_id=MemberId(
                p.recipient_member_id or cycles[p.cycle_id].payout_member_id or ""
            ),
            batch_id=p.paypal_batch_id or p.sender_batch_id,
            amount=p.amount.quantize(CENT),
            status=_PAYOUT_STATUS.get(p.status, PayoutStatus.FAILED),
            created_at=p.created_at,
        )
        for p in payouts
    ]
    collected = sum(
        (c.amount for c in contributions if c.status in ("PAID", "COVERED")), Decimal("0")
    )
    paid_out = sum(
        (p.amount for p in payouts if p.status in _ACCEPTED or p.paypal_batch_id),
        Decimal("0"),
    )
    return LedgerResponse(
        circle_id=CircleId(circle_id),
        rows=rows,
        payouts=payout_rows,
        contributions_total=collected.quantize(CENT),
        paid_out_total=paid_out.quantize(CENT),
    )


def _tool_calls(raw: str | None) -> list[ToolCall]:
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    calls: list[ToolCall] = []
    for item in items if isinstance(items, list) else []:
        try:
            calls.append(ToolCall.model_validate(item))
        except ValidationError:
            continue
    return calls


def audit(db: Session, circle_id: str) -> AuditResponse:
    rows = db.scalars(
        select(AgentDecision)
        .where(AgentDecision.circle_id == circle_id)
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
    ).all()
    decisions = []
    for r in rows:
        try:
            agent = AgentName(r.agent)
        except ValueError:
            continue
        decisions.append(
            AgentDecisionContract(
                decision_id=r.id,
                agent=agent,
                circle_id=CircleId(circle_id),
                model=r.model or "unknown",
                tool_calls=_tool_calls(r.tools_json),
                rationale=r.rationale or r.decision or "",
                created_at=r.created_at,
                human_approved=r.actor is not None,
            )
        )
    return AuditResponse(circle_id=CircleId(circle_id), decisions=decisions)
