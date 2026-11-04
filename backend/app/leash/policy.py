"""Versioned per-circle Leash policy. Callers own the transaction (flush only)."""

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..schemas.contracts import (
    CircleId,
    LeashPolicy,
    LeashPreset,
    LeashThresholds,
    MemberId,
)
from .ids import new_id
from .models import LeashPolicyRow


def _to_policy(row: LeashPolicyRow) -> LeashPolicy:
    return LeashPolicy(
        circle_id=CircleId(row.circle_id),
        version=row.version,
        preset=cast("LeashPreset", row.preset),
        thresholds=LeashThresholds.model_validate(row.thresholds_json),
        no_auto_chase_member_ids=[MemberId(x) for x in row.no_auto_chase_json],
    )


def _active_row(db: Session, circle_id: str) -> LeashPolicyRow | None:
    return db.scalars(
        select(LeashPolicyRow).where(
            LeashPolicyRow.circle_id == circle_id, LeashPolicyRow.active.is_(True)
        )
    ).one_or_none()


def save_policy(
    db: Session,
    circle_id: str,
    preset: LeashPreset,
    thresholds: LeashThresholds,
    no_auto_chase_member_ids: list[str],
) -> LeashPolicy:
    """Deactivate the current row, then insert version n + 1 as active."""
    current = _active_row(db, circle_id)
    if current is not None:
        current.active = False
        db.flush()  # free the partial unique index before inserting
    max_version = db.scalar(
        select(func.max(LeashPolicyRow.version)).where(LeashPolicyRow.circle_id == circle_id)
    )
    thresholds_json: dict[str, Any] = thresholds.model_dump(mode="json")
    row = LeashPolicyRow(
        id=new_id("lpol"),
        circle_id=circle_id,
        version=(max_version or 0) + 1,
        preset=preset,
        thresholds_json=thresholds_json,
        no_auto_chase_json=list(no_auto_chase_member_ids),
        active=True,
    )
    db.add(row)
    db.flush()
    return _to_policy(row)


def create_initial_policy(
    db: Session, circle_id: str, preset: LeashPreset = "STANDARD"
) -> LeashPolicy:
    return save_policy(db, circle_id, preset, LeashThresholds(), [])


def get_active_policy(db: Session, circle_id: str) -> LeashPolicy:
    row = _active_row(db, circle_id)
    if row is None:
        return create_initial_policy(db, circle_id)
    return _to_policy(row)
