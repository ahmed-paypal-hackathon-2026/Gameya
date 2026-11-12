"""DEMO_MODE-only admin routes. Engine-backed fast-forward/reset land with WP-1.3.

They exist only when DEMO_MODE is on. In production they also need the
X-Admin-Token header to match ADMIN_TOKEN, so the public demo cannot be reset or
fast-forwarded by anyone who finds the URL.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..db.models import Cycle
from ..db.seed import seed_golden_circle
from ..domain import clock
from ..schemas.contracts import FastForwardRequest, ResetDemoResponse

# Past the one-day grace period, so Smart Chase acts on the late member.
FAST_FORWARD_PAST_DUE = timedelta(days=2)


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="admin routes only exist in DEMO_MODE")
    if settings.is_production and not (
        settings.admin_token
        and x_admin_token is not None
        and hmac.compare_digest(x_admin_token, settings.admin_token)
    ):
        raise HTTPException(status_code=403, detail="a valid X-Admin-Token is required")


router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/cycles/{cycle_id}/fast-forward")
def fast_forward(
    cycle_id: str, body: FastForwardRequest, db: Annotated[Session, Depends(get_db)]
) -> dict[str, object]:
    """Move the engine clock past the grace period of cycle `to_cycle_index` of this
    cycle's circle. Only the clock moves: no payment state is changed."""
    cycle = db.get(Cycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="cycle not found")
    target = db.scalars(
        select(Cycle).where(
            Cycle.circle_id == cycle.circle_id, Cycle.cycle_index == body.to_cycle_index
        )
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="no such cycle in this circle")
    moment = datetime.combine(target.due_date, time(0, 0), tzinfo=UTC) + FAST_FORWARD_PAST_DUE
    now = clock.advance_to(moment)
    return {
        "fast_forwarded": True,
        "cycle_id": target.id,
        "to_cycle_index": body.to_cycle_index,
        "now": now.isoformat(),
    }


@router.post("/reset-demo", response_model=ResetDemoResponse)
def reset_demo(db: Annotated[Session, Depends(get_db)]) -> ResetDemoResponse:
    """Delete and recreate the golden demo circle in the database."""
    circle_id = seed_golden_circle(db)
    clock.reset()
    return ResetDemoResponse.model_validate({"reset": True, "circle_id": circle_id})
