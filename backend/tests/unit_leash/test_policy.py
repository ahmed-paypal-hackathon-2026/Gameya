from decimal import Decimal

from app.leash.models import LeashPolicyRow
from app.leash.policy import (
    create_initial_policy,
    get_active_policy,
    save_policy,
)
from app.schemas.contracts import LeashThresholds
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_initial_policy_v1_standard(db: Session) -> None:
    p = create_initial_policy(db, "c1")
    assert p.version == 1
    assert p.preset == "STANDARD"
    assert p.thresholds == LeashThresholds()
    assert p.no_auto_chase_member_ids == []


def test_save_creates_next_version_and_single_active(db: Session) -> None:
    create_initial_policy(db, "c1")
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    p3 = save_policy(db, "c1", "HANDS_OFF", LeashThresholds(), [])
    db.commit()
    rows = db.scalars(
        select(LeashPolicyRow)
        .where(LeashPolicyRow.circle_id == "c1")
        .order_by(LeashPolicyRow.version)
    ).all()
    assert [r.version for r in rows] == [1, 2, 3]
    assert [r.active for r in rows] == [False, False, True]
    assert p3.version == 3 and p3.preset == "HANDS_OFF"
    assert get_active_policy(db, "c1").version == 3


def test_get_active_lazily_creates_v1(db: Session) -> None:
    p = get_active_policy(db, "new")
    assert p.version == 1 and p.preset == "STANDARD"
    assert len(db.scalars(select(LeashPolicyRow)).all()) == 1
    assert get_active_policy(db, "new").version == 1
    assert len(db.scalars(select(LeashPolicyRow)).all()) == 1


def test_save_without_existing_policy_starts_at_v1(db: Session) -> None:
    assert save_policy(db, "c9", "STRICT", LeashThresholds(), []).version == 1


def test_no_auto_chase_round_trip(db: Session) -> None:
    th = LeashThresholds(high_risk_p_late=Decimal("0.50"), swap_cooldown_hours=12)
    save_policy(db, "c1", "STRICT", th, ["m1", "m2"])
    db.commit()
    db.expire_all()
    p = get_active_policy(db, "c1")
    assert p.no_auto_chase_member_ids == ["m1", "m2"]
    assert p.thresholds == th
    assert p.thresholds.high_risk_p_late == Decimal("0.50")
    assert isinstance(p.thresholds.high_risk_p_late, Decimal)
    assert p.preset == "STRICT" and p.circle_id == "c1"


def test_policy_rows_use_lpol_prefix(db: Session) -> None:
    create_initial_policy(db, "c1")
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    ids = db.scalars(select(LeashPolicyRow.id)).all()
    assert len(ids) == 2
    assert all(i.startswith("lpol_") and len(i) <= 32 for i in ids)
