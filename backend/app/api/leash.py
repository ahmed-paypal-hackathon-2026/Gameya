"""Leash REST router. Person 1 mounts it, calls register_leash_errors and overrides the deps."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from ulid import ULID

from ..leash import LeashError, leash_enabled, resolve
from ..leash.models import LeashDecisionRecord
from ..leash.policy import get_active_policy, save_policy
from ..schemas.contracts import (
    GuardAction,
    LeashDecision,
    LeashDecisionRow,
    LeashErrorCode,
    LeashInbox,
    LeashInboxItem,
    LeashInboxRecipient,
    LeashPolicy,
    LeashPreset,
    LeashResolveRequest,
    LeashResolveResponse,
    LeashThresholds,
)

router = APIRouter(prefix="/api/v1", tags=["leash"])


@dataclass(frozen=True)
class Actor:
    user_id: str
    organiser_of: frozenset[str]


def get_db() -> Iterator[Session]:
    """Placeholder: Person 1 overrides this with the real session dependency."""
    raise NotImplementedError("get_db must be overridden when mounting the Leash router")


def get_actor() -> Actor:
    """Placeholder: Person 1 overrides this with the authenticated actor."""
    raise NotImplementedError("get_actor must be overridden when mounting the Leash router")


DbDep = Annotated[Session, Depends(get_db)]
ActorDep = Annotated[Actor, Depends(get_actor)]


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or f"req_{ULID()}"


def _envelope(request: Request, exc: LeashError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "details": exc.details,
                "request_id": _request_id(request),
            }
        },
    )


def register_leash_errors(app: FastAPI) -> None:
    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, LeashError)
        return _envelope(request, exc)

    app.add_exception_handler(LeashError, _handler)


def _require_organiser(actor: Actor, circle_id: str) -> None:
    if circle_id not in actor.organiser_of:
        raise LeashError(
            LeashErrorCode.ORGANISER_ONLY,
            "Only the circle organiser can do this.",
            {"circle_id": circle_id},
        )


class _PolicyPayload(BaseModel):
    """PUT body: a LeashPolicy minus the server-controlled circle_id and version."""

    preset: LeashPreset = "STANDARD"
    thresholds: LeashThresholds = Field(default_factory=LeashThresholds)
    no_auto_chase_member_ids: list[str] = Field(default_factory=list)


def _invalid_policy(errors: list[dict[str, Any]]) -> LeashError:
    return LeashError(
        LeashErrorCode.LEASH_POLICY_INVALID,
        "The Leash policy is invalid.",
        {"errors": errors},
    )


@router.get("/circles/{cid}/leash/policy", response_model=LeashPolicy)
def get_policy(cid: str, db: DbDep, _actor: ActorDep) -> LeashPolicy:
    policy = get_active_policy(db, cid)
    db.commit()  # persist a lazily created v1
    return policy


@router.put("/circles/{cid}/leash/policy", response_model=LeashPolicy)
async def put_policy(
    cid: str,
    request: Request,
    db: DbDep,
    actor: ActorDep,
) -> LeashPolicy:
    _require_organiser(actor, cid)
    try:
        raw = await request.json()
    except ValueError as exc:
        raise _invalid_policy([{"loc": [], "msg": "Request body is not valid JSON."}]) from exc
    try:
        payload = _PolicyPayload.model_validate(raw)
    except ValidationError as exc:
        raise _invalid_policy(
            [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        ) from exc
    policy = save_policy(
        db, cid, payload.preset, payload.thresholds, payload.no_auto_chase_member_ids
    )
    db.commit()
    return policy


def _utc(value: datetime) -> datetime:
    # SQLite hands datetimes back naive; they were stored as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _recipient(row: LeashDecisionRecord) -> LeashInboxRecipient | None:
    facts = row.facts_json
    if row.subject_type == "payout" and "recipient_member_id" in facts:
        return LeashInboxRecipient(
            member_id=facts["recipient_member_id"],
            name=facts["recipient_name"],
            payout_email=facts.get("recipient_payout_email"),
        )
    if row.subject_type != "swap" and "member_id" in facts:
        return LeashInboxRecipient(
            member_id=facts["member_id"], name=facts["member_name"], payout_email=None
        )
    return None


def _inbox_item(row: LeashDecisionRecord) -> LeashInboxItem:
    return LeashInboxItem(
        decision_id=row.id,
        action=GuardAction(row.action),
        subject_type=row.subject_type,  # type: ignore[arg-type]
        subject_id=row.subject_id,
        cycle_index=row.facts_json.get("cycle_index"),
        recipient=_recipient(row),
        amount=row.amount,
        rule_ids=list(row.rule_ids_json),
        reason=row.reason,
        created_at=_utc(row.created_at),
    )


def _decision_row(row: LeashDecisionRecord) -> LeashDecisionRow:
    return LeashDecisionRow(
        decision_id=row.id,
        circle_id=row.circle_id,
        policy_version=row.policy_version,
        action=GuardAction(row.action),
        subject_type=row.subject_type,  # type: ignore[arg-type]
        subject_id=row.subject_id,
        decision=LeashDecision(row.decision),
        rule_ids=list(row.rule_ids_json),
        amount=row.amount,
        reason=row.reason,
        actor=row.actor,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        created_at=_utc(row.created_at),
        resolved_at=_utc(row.resolved_at) if row.resolved_at else None,
    )


@router.get("/circles/{cid}/leash/inbox", response_model=LeashInbox)
def get_inbox(cid: str, db: DbDep, actor: ActorDep) -> LeashInbox:
    _require_organiser(actor, cid)
    if not leash_enabled():
        raise LeashError(LeashErrorCode.LEASH_DISABLED, "Leash is switched off.")
    rows = db.scalars(
        select(LeashDecisionRecord)
        .where(LeashDecisionRecord.circle_id == cid, LeashDecisionRecord.status == "OPEN")
        .order_by(LeashDecisionRecord.created_at.asc(), LeashDecisionRecord.id.asc())
    )
    return LeashInbox(items=[_inbox_item(r) for r in rows])


@router.get("/circles/{cid}/leash/decisions", response_model=list[LeashDecisionRow])
def get_decisions(cid: str, db: DbDep, actor: ActorDep, limit: int = 100) -> list[LeashDecisionRow]:
    _require_organiser(actor, cid)
    capped = max(1, min(limit, 500))
    rows = db.scalars(
        select(LeashDecisionRecord)
        .where(LeashDecisionRecord.circle_id == cid)
        .order_by(LeashDecisionRecord.created_at.desc(), LeashDecisionRecord.id.desc())
        .limit(capped)
    )
    return [_decision_row(r) for r in rows]


def _resolve(
    did: str, approve: bool, body: LeashResolveRequest | None, db: Session, actor: Actor
) -> LeashResolveResponse:
    row = db.get(LeashDecisionRecord, did)
    if row is None:
        raise LeashError(
            LeashErrorCode.LEASH_DECISION_NOT_FOUND,
            "Leash decision not found.",
            {"decision_id": did},
        )
    _require_organiser(actor, row.circle_id)
    try:
        response = resolve(db, did, approve, actor.user_id, body.note if body else None)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return response


@router.post("/leash/decisions/{did}/approve", response_model=LeashResolveResponse)
def approve_decision(
    did: str, db: DbDep, actor: ActorDep, body: LeashResolveRequest | None = None
) -> LeashResolveResponse:
    return _resolve(did, True, body, db, actor)


@router.post("/leash/decisions/{did}/reject", response_model=LeashResolveResponse)
def reject_decision(
    did: str, db: DbDep, actor: ActorDep, body: LeashResolveRequest | None = None
) -> LeashResolveResponse:
    return _resolve(did, False, body, db, actor)
