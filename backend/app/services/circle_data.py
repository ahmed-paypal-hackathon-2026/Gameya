"""Circle rows in the database and the REST views built from them (plan Task 5).

`create_circle_rows` stores a circle the organiser confirmed, with the PayPal product
and plan ids that the Circle Agent created. Seats are filled from the demo roster
(the six sandbox personas), organiser first; a circle larger than the roster gets
open seats that stay inactive until someone joins. `circle_detail` reads the rows back
as the `CircleDetail` contract, so GET and POST answer with the same shape.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.fixtures import fixture
from ..db.models import (
    AgentDecision,
    Circle,
    Cycle,
    Invoice,
    Membership,
    NeedRecord,
    OrderApproval,
    OrderProposalRecord,
    Payout,
)
from ..db.seed import buyer_email
from ..ml.reliability.personas import PERSONA_FEATURES
from ..ml.reliability.scorer import score_reliability
from ..schemas.contracts import (
    AgentDecision as AgentDecisionContract,
)
from ..schemas.contracts import (
    CircleDetail,
    CircleId,
    CircleSnapshot,
    CircleSpec,
    CycleSnapshot,
    CycleStatus,
    CycleUnit,
    MemberId,
    MemberProfile,
    MemberSnapshot,
    NeedExtraction,
    OrderProposal,
    ReliabilityFeatures,
    ReliabilityScore,
    Role,
    SlotDate,
)

CENT = Decimal("0.01")


def cycle_due_date(start: date, unit: CycleUnit | str, index: int) -> date:
    """Due date of cycle `index` (0-based): weekly or monthly from the start date."""
    if CycleUnit(unit) is CycleUnit.WEEK:
        return start + timedelta(weeks=index)
    year, month = divmod(start.month - 1 + index, 12)
    last_day = calendar.monthrange(start.year + year, month + 1)[1]
    return date(start.year + year, month + 1, min(start.day, last_day))


def _roster(organiser_id: str) -> list[dict[str, str]]:
    """The demo personas, organiser first."""
    members = [
        {"member_id": m["member_id"], "name": m["display_name"], "locale": m["locale"]}
        for m in fixture("circle-get")["members"]
    ]
    first = [m for m in members if m["member_id"] == organiser_id]
    rest = [m for m in members if m["member_id"] != organiser_id]
    if not first:
        first = [{"member_id": organiser_id, "name": organiser_id, "locale": "en"}]
    return first + rest


def create_circle_rows(
    db: Session,
    *,
    circle_id: str,
    spec: CircleSpec,
    organiser_id: str,
    reserve_pct: Decimal,
    product_id: str | None,
    plan_id: str | None,
) -> None:
    """Insert the circle, its seats and its cycles (flushes; the caller commits)."""
    db.add(
        Circle(
            id=circle_id,
            name=spec.name,
            contribution_amount=spec.contribution_amount,
            currency=spec.currency,
            cycle_unit=spec.cycle_unit.value,
            size_n=spec.member_count,
            start_date=spec.start_date,
            reserve_pct=reserve_pct,
            organiser_member_id=organiser_id,
            paypal_product_id=product_id,
            paypal_plan_id=plan_id,
            status="PLANNED",
        )
    )
    db.flush()  # the circle must exist before rows that reference it
    roster = _roster(organiser_id)
    for seat in range(spec.member_count):
        if seat < len(roster):
            person = roster[seat]
            member_id, name, locale, active = (
                person["member_id"],
                person["name"],
                person["locale"],
                True,
            )
        else:
            member_id, name, locale, active = (
                f"mem-seat-{seat + 1}",
                f"Open seat {seat + 1}",
                "en",
                False,
            )
        db.add(
            Membership(
                id=f"ms-{circle_id}-{member_id}",
                circle_id=circle_id,
                member_id=member_id,
                name=name,
                locale=locale,
                email=buyer_email(member_id),
                is_organiser=member_id == organiser_id,
                is_active=active,
                slot_index=None,  # decided by the fair order, then approved
            )
        )
    for index in range(spec.member_count):
        db.add(
            Cycle(
                id=f"{circle_id}-c{index}",
                circle_id=circle_id,
                cycle_index=index,
                due_date=cycle_due_date(spec.start_date, spec.cycle_unit, index),
                status="UPCOMING",
            )
        )
    db.flush()


def record_decision(db: Session, decision: AgentDecisionContract, actor: str) -> None:
    """Store an agent decision for the audit log (tool calls as JSON, no secrets)."""
    db.add(
        AgentDecision(
            id=decision.decision_id,
            circle_id=decision.circle_id,
            agent=decision.agent.value,
            decision=decision.rationale,
            model=decision.model,
            tools_json=json.dumps(
                [call.model_dump(mode="json") for call in decision.tool_calls], sort_keys=True
            ),
            rationale=decision.rationale,
            actor=actor,
        )
    )
    db.flush()


def _cycle_status(value: str) -> CycleStatus:
    try:
        return CycleStatus(value.lower())
    except ValueError:
        return CycleStatus.UPCOMING


def circle_detail(db: Session, circle_id: str) -> CircleDetail | None:
    """The `CircleDetail` view of a stored circle, or None if there is no such circle."""
    circle = db.get(Circle, circle_id)
    if circle is None:
        return None
    memberships = db.scalars(
        select(Membership).where(Membership.circle_id == circle_id).order_by(Membership.id)
    ).all()
    seats = sorted(memberships, key=lambda m: (not m.is_organiser, m.joined_at, m.id))
    cycles = db.scalars(
        select(Cycle).where(Cycle.circle_id == circle_id).order_by(Cycle.cycle_index)
    ).all()
    amount = circle.contribution_amount.quantize(CENT)
    pot = (amount * circle.size_n).quantize(CENT)
    current = next((c.cycle_index for c in cycles if c.status not in ("PAID_OUT",)), 0)
    return CircleDetail(
        circle_id=CircleId(circle.id),
        spec=CircleSpec(
            name=circle.name,
            member_count=circle.size_n,
            contribution_amount=amount,
            currency="USD",
            cycle_unit=CycleUnit(circle.cycle_unit),
            start_date=circle.start_date,
            organiser_id=MemberId(circle.organiser_member_id)
            if circle.organiser_member_id
            else None,
            confirmed=True,
        ),
        members=[
            MemberSnapshot(
                member_id=MemberId(m.member_id),
                display_name=m.name,
                role=Role.ORGANISER if m.is_organiser else Role.MEMBER,
                locale="ar-EG" if m.locale == "ar-EG" else "en",
                slot_index=m.slot_index,
            )
            for m in seats
        ],
        cycles=[
            CycleSnapshot(
                cycle_index=c.cycle_index,
                status=_cycle_status(c.status),
                due_date=c.due_date,
                amount_due=amount,
                pot_total=pot,
                recipient_id=MemberId(c.payout_member_id) if c.payout_member_id else None,
            )
            for c in cycles
        ],
        current_cycle_index=current,
        reserve_pct=circle.reserve_pct,
    )


def organised_circle_ids(db: Session, member_id: str) -> frozenset[str]:
    """Circles this member organises, from the memberships table."""
    rows = db.scalars(
        select(Membership.circle_id).where(
            Membership.member_id == member_id, Membership.is_organiser.is_(True)
        )
    ).all()
    return frozenset(rows)


# ---------- P3 engine inputs (plan Task 6) ----------
# A member with no payment history yet: a fresh account, nothing late, nothing completed.
NEW_MEMBER_FEATURES = ReliabilityFeatures(
    on_time_ratio=1.0,
    avg_days_late=0.0,
    cycles_completed=0,
    failed_payments=0,
    invoices_needed=0,
    circles_completed=0,
    account_age_days=30,
)


def circle_snapshot(db: Session, circle_id: str) -> CircleSnapshot | None:
    circle = db.get(Circle, circle_id)
    if circle is None:
        return None
    return CircleSnapshot(
        circle_id=CircleId(circle.id),
        name=circle.name,
        contribution_amount=circle.contribution_amount.quantize(CENT),
        currency="USD",
        cycle_unit=CycleUnit(circle.cycle_unit),
        size_n=circle.size_n,
        start_date=circle.start_date,
        reserve_pct=circle.reserve_pct,
    )


def active_members(db: Session, circle_id: str) -> list[Membership]:
    """Active seats, in a stable order (by member id)."""
    return list(
        db.scalars(
            select(Membership)
            .where(Membership.circle_id == circle_id, Membership.is_active.is_(True))
            .order_by(Membership.member_id)
        ).all()
    )


def is_member(db: Session, circle_id: str, member_id: str) -> bool:
    return any(m.member_id == member_id for m in active_members(db, circle_id))


def reliability_scores(db: Session, circle_id: str) -> dict[MemberId, ReliabilityScore]:
    """P(late) per active member. Demo personas use their simulated histories
    (PERSONA_FEATURES); anyone else is scored as a new member with no history."""
    scores: dict[MemberId, ReliabilityScore] = {}
    for m in active_members(db, circle_id):
        member_id = MemberId(m.member_id)
        features = PERSONA_FEATURES.get(m.member_id.removeprefix("mem-"), NEW_MEMBER_FEATURES)
        scores[member_id] = score_reliability(features, member_id)
    return scores


def save_need(db: Session, circle_id: str, need: NeedExtraction, source: str) -> None:
    """Store (or replace) a member's need."""
    if need.member_id is None:
        raise ValueError("a stored need must name its member")
    row = db.scalars(
        select(NeedRecord).where(
            NeedRecord.circle_id == circle_id, NeedRecord.member_id == need.member_id
        )
    ).first()
    payload = need.model_dump_json()
    if row is None:
        db.add(
            NeedRecord(
                id=f"need-{circle_id}-{need.member_id}"[:64],
                circle_id=circle_id,
                member_id=need.member_id,
                need_json=payload,
                source=source,
            )
        )
    else:
        row.need_json, row.source = payload, source
    db.flush()


def list_needs(db: Session, circle_id: str) -> list[NeedExtraction]:
    rows = db.scalars(
        select(NeedRecord).where(NeedRecord.circle_id == circle_id).order_by(NeedRecord.member_id)
    ).all()
    return [NeedExtraction.model_validate_json(r.need_json) for r in rows]


def member_profiles(db: Session, circle_id: str) -> list[MemberProfile]:
    """Fair-order inputs: each active member with their need and P(late)."""
    needs = {n.member_id: n for n in list_needs(db, circle_id)}
    scores = reliability_scores(db, circle_id)
    return [
        MemberProfile(
            member=MemberSnapshot(
                member_id=MemberId(m.member_id),
                display_name=m.name,
                role=Role.ORGANISER if m.is_organiser else Role.MEMBER,
                locale="ar-EG" if m.locale == "ar-EG" else "en",
            ),
            need=needs.get(MemberId(m.member_id)),
            reliability_p_late=scores[MemberId(m.member_id)].p_late,
        )
        for m in active_members(db, circle_id)
    ]


def slot_dates(db: Session, circle_id: str) -> list[SlotDate]:
    """One payout slot per cycle: slot_index is the cycle index, the date its due date."""
    cycles = db.scalars(
        select(Cycle).where(Cycle.circle_id == circle_id).order_by(Cycle.cycle_index)
    ).all()
    return [SlotDate(slot_index=c.cycle_index, payout_date=c.due_date) for c in cycles]


def order_frozen(db: Session, circle_id: str) -> bool:
    """True once any invoice or payout exists: from then on the payout order is fixed,
    so a new proposal can never move a recipient of a cycle already collected or paid."""
    if db.scalars(select(Invoice.id).where(Invoice.circle_id == circle_id)).first():
        return True
    cycle_ids = select(Cycle.id).where(Cycle.circle_id == circle_id)
    return db.scalars(select(Payout.id).where(Payout.cycle_id.in_(cycle_ids))).first() is not None


def current_proposal(db: Session, circle_id: str) -> OrderProposal | None:
    row = db.get(OrderProposalRecord, circle_id)
    return None if row is None else OrderProposal.model_validate_json(row.proposal_json)


def same_order(a: OrderProposal, b: OrderProposal) -> bool:
    return _proposal_hash(a) == _proposal_hash(b)


def _proposal_hash(proposal: OrderProposal) -> str:
    order = [(a.slot_index, a.member_id) for a in proposal.assignments]
    return hashlib.sha256(json.dumps([proposal.seed, order]).encode()).hexdigest()


def save_proposal(db: Session, circle_id: str, proposal: OrderProposal) -> None:
    """Make this the circle's current proposal. Approvals of an older, different
    proposal stop counting, because each approval is tied to a proposal hash."""
    digest = _proposal_hash(proposal)
    row = db.get(OrderProposalRecord, circle_id)
    if row is None:
        db.add(
            OrderProposalRecord(
                circle_id=circle_id, proposal_hash=digest, proposal_json=proposal.model_dump_json()
            )
        )
    else:
        row.proposal_hash, row.proposal_json = digest, proposal.model_dump_json()
    db.flush()


def approve_order(db: Session, circle_id: str, member_id: str) -> OrderProposal:
    """Record one member's approval of the current proposal (approving twice is a no-op).

    When every active member has approved, the order is applied: seats get their slot
    and each cycle its recipient. Raises LookupError if nothing was proposed yet.
    """
    row = db.get(OrderProposalRecord, circle_id)
    if row is None:
        raise LookupError("no order has been proposed yet")
    already = db.scalars(
        select(OrderApproval).where(
            OrderApproval.circle_id == circle_id,
            OrderApproval.member_id == member_id,
            OrderApproval.proposal_hash == row.proposal_hash,
        )
    ).first()
    if already is None:
        db.add(
            OrderApproval(
                id=f"oa-{row.proposal_hash[:16]}-{member_id}"[:64],
                circle_id=circle_id,
                member_id=member_id,
                proposal_hash=row.proposal_hash,
            )
        )
        db.flush()
    proposal = OrderProposal.model_validate_json(row.proposal_json)
    approved = set(
        db.scalars(
            select(OrderApproval.member_id).where(
                OrderApproval.circle_id == circle_id,
                OrderApproval.proposal_hash == row.proposal_hash,
            )
        ).all()
    )
    seats = active_members(db, circle_id)
    if order_frozen(db, circle_id):
        return proposal  # money has moved: the order applied before stays
    if seats and all(m.member_id in approved for m in seats):
        slot_of = {a.member_id: a.slot_index for a in proposal.assignments}
        for m in seats:
            m.slot_index = slot_of.get(MemberId(m.member_id))
        recipient = {a.slot_index: a.member_id for a in proposal.assignments}
        for c in db.scalars(select(Cycle).where(Cycle.circle_id == circle_id)).all():
            c.payout_member_id = recipient.get(c.cycle_index)
        db.flush()
    return proposal
