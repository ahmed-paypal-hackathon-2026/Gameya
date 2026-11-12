"""The golden demo circle in the database (plan WP-5.2 / TST-20, review I5).

`seed_golden_circle` deletes the golden circle and everything that hangs off it, then
recreates it from shared/fixtures/json/circle-get.json, so running it twice gives the
same rows. Members get the sandbox buyer emails the demo personas use; create those
accounts in the PayPal sandbox with exactly these addresses.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..api.fixtures import fixture
from .models import (
    AgentDecision,
    Circle,
    Contribution,
    Cycle,
    Invoice,
    Membership,
    NeedRecord,
    OrderApproval,
    OrderProposalRecord,
    Payout,
)

GOLDEN_CIRCLE_ID = "circle-golden-01"
SANDBOX_EMAIL_DOMAIN = "gameya-sandbox.dev"


def buyer_email(member_id: str) -> str:
    """mem-amina -> amina-buyer@gameya-sandbox.dev (the persona's sandbox account)."""
    return f"{member_id.removeprefix('mem-')}-buyer@{SANDBOX_EMAIL_DOMAIN}"


def _add_months(start: date, months: int) -> date:
    year, month = divmod(start.month - 1 + months, 12)
    return date(start.year + year, month + 1, start.day)


def delete_circle(session: Session, circle_id: str) -> None:
    """Remove a circle and every row that references it (children first)."""
    cycle_ids = select(Cycle.id).where(Cycle.circle_id == circle_id)
    session.execute(delete(Invoice).where(Invoice.circle_id == circle_id))
    session.execute(delete(Contribution).where(Contribution.cycle_id.in_(cycle_ids)))
    session.execute(delete(Payout).where(Payout.cycle_id.in_(cycle_ids)))
    session.execute(delete(AgentDecision).where(AgentDecision.circle_id == circle_id))
    session.execute(delete(NeedRecord).where(NeedRecord.circle_id == circle_id))
    session.execute(delete(OrderApproval).where(OrderApproval.circle_id == circle_id))
    session.execute(delete(OrderProposalRecord).where(OrderProposalRecord.circle_id == circle_id))
    session.execute(delete(Membership).where(Membership.circle_id == circle_id))
    session.execute(delete(Cycle).where(Cycle.circle_id == circle_id))
    session.execute(delete(Circle).where(Circle.id == circle_id))
    session.flush()


def seed_golden_circle(session: Session) -> str:
    """Recreate the golden circle (6 members, 6 monthly cycles); returns its id."""
    data = fixture("circle-get")
    spec = data["spec"]
    circle_id = str(data["circle_id"])
    start = date.fromisoformat(spec["start_date"])
    size = int(spec["member_count"])

    delete_circle(session, circle_id)
    session.add(
        Circle(
            id=circle_id,
            name=spec["name"],
            contribution_amount=Decimal(spec["contribution_amount"]),
            currency=spec["currency"],
            cycle_unit=spec["cycle_unit"],
            size_n=size,
            start_date=start,
            reserve_pct=Decimal(str(data.get("reserve_pct", "0"))),
            organiser_member_id=spec["organiser_id"],
            status="PLANNED",
        )
    )
    session.flush()  # the circle must exist before rows that reference it
    for member in data["members"]:
        member_id = member["member_id"]
        session.add(
            Membership(
                id=f"ms-{circle_id}-{member_id}",
                circle_id=circle_id,
                member_id=member_id,
                name=member["display_name"],
                locale=member["locale"],
                email=buyer_email(member_id),
                is_organiser=member["role"] == "organiser",
                is_active=True,
                slot_index=None,  # decided by the fair order, then approved
            )
        )
    for index in range(size):
        session.add(
            Cycle(
                id=f"{circle_id}-c{index}",
                circle_id=circle_id,
                cycle_index=index,
                due_date=_add_months(start, index),
                status="UPCOMING",
            )
        )
    session.flush()
    return circle_id
