"""Circle rows in the database and the REST views built from them (plan Task 5).

`create_circle_rows` stores a circle the organiser confirmed, with the PayPal product
and plan ids that the Circle Agent created. Seats are filled from the demo roster
(the six sandbox personas), organiser first; a circle larger than the roster gets
open seats that stay inactive until someone joins. `circle_detail` reads the rows back
as the `CircleDetail` contract, so GET and POST answer with the same shape.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.fixtures import fixture
from ..db.models import AgentDecision, Circle, Cycle, Membership
from ..db.seed import buyer_email
from ..schemas.contracts import (
    AgentDecision as AgentDecisionContract,
)
from ..schemas.contracts import (
    CircleDetail,
    CircleId,
    CircleSpec,
    CycleSnapshot,
    CycleStatus,
    CycleUnit,
    MemberId,
    MemberSnapshot,
    Role,
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
