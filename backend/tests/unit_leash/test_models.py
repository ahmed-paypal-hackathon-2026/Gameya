from datetime import UTC, datetime

import pytest
from app.leash.models import LeashDecisionRecord, LeashPolicyRow
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _policy(circle: str, version: int, active: bool) -> LeashPolicyRow:
    return LeashPolicyRow(
        id=f"lpol_{circle}{version}",
        circle_id=circle,
        version=version,
        preset="STANDARD",
        thresholds_json={},
        no_auto_chase_json=[],
        active=active,
    )


def test_begin_nested_rollback_works(db: Session) -> None:
    db.add(_policy("c1", 1, True))
    db.flush()
    nested = db.begin_nested()
    db.add(_policy("c2", 1, True))
    db.flush()
    nested.rollback()
    circles = db.scalars(select(LeashPolicyRow.circle_id)).all()
    assert circles == ["c1"]


def test_second_active_row_violates_partial_unique_index(db: Session) -> None:
    db.add(_policy("c1", 1, True))
    db.flush()
    db.add(_policy("c1", 2, True))
    with pytest.raises(IntegrityError):
        db.flush()


def test_inactive_rows_may_coexist_and_other_circles_independent(db: Session) -> None:
    db.add_all([_policy("c1", 1, False), _policy("c1", 2, False), _policy("c1", 3, True)])
    db.add(_policy("c2", 1, True))
    db.flush()


def test_duplicate_version_rejected(db: Session) -> None:
    db.add(_policy("c1", 1, False))
    db.flush()
    dup = _policy("c1", 1, False)
    dup.id = "lpol_other"
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()


def test_decision_indexes_and_defaults(db: Session) -> None:
    names = {i["name"] for i in inspect(db.get_bind()).get_indexes("leash_decisions")}
    assert {"ix_leash_subject", "ix_leash_open"} <= names
    row = LeashDecisionRecord(
        id="lsd_1",
        circle_id="c1",
        policy_version=1,
        action="RELEASE_POT_PAYOUT",
        subject_type="payout",
        subject_id="p1",
        decision="ALLOW",
        rule_ids_json=[],
        facts_json={},
        reason="ok",
        actor="leash",
        status="FINAL",
    )
    db.add(row)
    db.flush()
    assert row.approves_id is None and row.resolved_at is None and row.amount is None
    assert isinstance(row.created_at, datetime) and row.created_at.tzinfo is not None
    assert row.created_at.utcoffset() == UTC.utcoffset(None)
