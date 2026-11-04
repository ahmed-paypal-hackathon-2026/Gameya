"""Leash service: build facts, evaluate rules, log the decision. Never commits."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from ..schemas.contracts import (
    GuardAction,
    GuardResult,
    LeashDecision,
    LeashErrorCode,
    LeashResolveResponse,
)
from .facts import BaseFacts, ChaseInvoiceFacts, FactsProvider, PayoutFacts
from .guard import evaluate_rules
from .ids import new_id
from .models import LeashDecisionRecord
from .policy import get_active_policy
from .templates import MAX_REASON_LEN, render_reason

_DISABLED_VALUES = {"false", "0", "no"}
DISABLED_RULE = "LEASH_DISABLED"
_SUBJECT_TYPES = {"payout", "chase_plan", "swap"}

_HTTP_STATUS: dict[LeashErrorCode, int] = {
    LeashErrorCode.ORGANISER_ONLY: 401,
    LeashErrorCode.LEASH_DECISION_NOT_FOUND: 404,
    LeashErrorCode.LEASH_ALREADY_RESOLVED: 409,
    LeashErrorCode.LEASH_STALE_DECISION: 409,
    LeashErrorCode.LEASH_POLICY_INVALID: 422,
    LeashErrorCode.LEASH_DISABLED: 503,
}


class LeashError(Exception):
    """A Leash failure the API layer maps to an HTTP error."""

    def __init__(
        self, code: LeashErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]


class EngineResume(Protocol):
    def resume(
        self, db: Session, action: GuardAction, subject_id: str, approved_decision_id: str
    ) -> dict[str, Any]:
        """Re-run the held action after an owner approved it."""
        ...

    def cancel(self, db: Session, action: GuardAction, subject_id: str, decision_id: str) -> None:
        """Stop the held action after an owner rejected it."""
        ...


_facts: FactsProvider | None = None
_engine: EngineResume | None = None


def configure(facts: FactsProvider, engine: EngineResume) -> None:
    """Register the facts provider and engine adapter (called once at startup)."""
    global _facts, _engine
    _facts = facts
    _engine = engine


def get_facts_provider() -> FactsProvider:
    if _facts is None:
        raise RuntimeError("Leash is not configured: call configure() first")
    return _facts


def get_engine() -> EngineResume:
    if _engine is None:
        raise RuntimeError("Leash is not configured: call configure() first")
    return _engine


def leash_enabled() -> bool:
    return os.environ.get("LEASH_ENABLED", "true").strip().lower() not in _DISABLED_VALUES


def _amount(facts: BaseFacts) -> Decimal | None:
    if isinstance(facts, PayoutFacts):
        return facts.amount
    if isinstance(facts, ChaseInvoiceFacts):
        return facts.invoice_amount
    return None


def _result(row: LeashDecisionRecord) -> GuardResult:
    return GuardResult(
        decision=LeashDecision(row.decision),
        rule_ids=list(row.rule_ids_json),
        reason=row.reason,
        decision_id=row.id,
    )


MAX_CHAIN_DEPTH = 10


def _approved_rule_ids(
    db: Session, action: GuardAction, subject_id: str, approved_decision_id: str
) -> list[str]:
    """Union of rule_ids along the approves_id chain, starting at the approved row.

    A resume re-check can raise a new ASK that carries ``approves_id`` = the ASK it re-checked, so
    approving the newest ASK must keep the waivers of every earlier approval on the chain.
    """
    row = db.get(LeashDecisionRecord, approved_decision_id)
    if (
        row is None
        or row.action != action.value
        or row.subject_id != subject_id
        or row.status != "APPROVED"
    ):
        raise ValueError(
            f"decision {approved_decision_id} is not an approved decision for this subject"
        )
    already_used = db.scalars(
        select(LeashDecisionRecord.id)
        .where(
            LeashDecisionRecord.approves_id == approved_decision_id,
            LeashDecisionRecord.actor == "leash",
        )
        .limit(1)
    ).first()
    if already_used is not None:
        raise ValueError(f"decision {approved_decision_id} has already been used")

    waived: list[str] = []
    current: LeashDecisionRecord | None = row
    depth = 0
    while current is not None:
        depth += 1
        if depth > MAX_CHAIN_DEPTH:
            raise ValueError(f"approval chain of {approved_decision_id} is deeper than 10")
        if current.action != action.value or current.subject_id != subject_id:
            raise ValueError(
                f"approval chain of {approved_decision_id} mixes different actions or subjects"
            )
        waived.extend(rule for rule in current.rule_ids_json if rule not in waived)
        current = (
            db.get(LeashDecisionRecord, current.approves_id)
            if current.approves_id is not None
            else None
        )
    return waived


def decide(
    db: Session,
    action: GuardAction,
    subject_id: str,
    approved_decision_id: str | None = None,
) -> GuardResult:
    """Guard one money action and log the outcome. Flushes only; the caller commits."""
    subject_type, facts = get_facts_provider().build(db, action, subject_id)
    if subject_type not in _SUBJECT_TYPES:
        raise ValueError(f"unknown subject_type {subject_type!r}; expected one of {_SUBJECT_TYPES}")

    if approved_decision_id is not None:
        facts.approved_rule_ids = _approved_rule_ids(db, action, subject_id, approved_decision_id)
    else:
        open_row = db.scalars(
            select(LeashDecisionRecord)
            .where(
                LeashDecisionRecord.action == action.value,
                LeashDecisionRecord.subject_id == subject_id,
                LeashDecisionRecord.status == "OPEN",
            )
            .order_by(LeashDecisionRecord.created_at.desc(), LeashDecisionRecord.id.desc())
        ).first()
        if open_row is not None:
            return _result(open_row)

    policy = get_active_policy(db, facts.circle_id)
    facts_json = facts.model_dump(mode="json")
    if leash_enabled():
        decision, rule_ids = evaluate_rules(policy, action, facts_json)
    else:
        decision, rule_ids = LeashDecision.ALLOW, [DISABLED_RULE]
    reason = render_reason(action, decision, rule_ids, facts)

    row = LeashDecisionRecord(
        id=new_id("lsd"),
        circle_id=facts.circle_id,
        policy_version=policy.version,
        action=action.value,
        subject_type=subject_type,
        subject_id=subject_id,
        decision=decision.value,
        rule_ids_json=rule_ids,
        facts_json=facts_json,
        amount=_amount(facts),
        reason=reason,
        actor="leash",
        status="OPEN" if decision is LeashDecision.ASK else "FINAL",
        approves_id=approved_decision_id,
    )
    db.add(row)
    db.flush()
    return _result(row)


def _organiser_reason(approve: bool, rule_ids: list[str], note: str | None) -> str:
    verb = "Approved" if approve else "Rejected"
    reason = f"{verb} by organiser: {rule_ids[0] if rule_ids else 'none'}."
    if note and note.strip():
        with_note = f"{reason} Note: {note.strip()}"
        if len(with_note) <= MAX_REASON_LEN:
            return with_note
    return reason


def resolve(
    db: Session,
    decision_id: str,
    approve: bool,
    actor_id: str,
    note: str | None = None,
) -> LeashResolveResponse:
    """Approve or reject one OPEN ASK decision exactly once. Flushes only; the caller commits.

    ``actor_id`` is accepted for the API layer's audit trail; the append-only schema has no
    column for it, so it is not stored. Everything runs in a SAVEPOINT, so an engine failure
    rolls back the claim and the organiser row and the exception propagates.
    """
    try:
        with db.begin_nested():
            ask = db.get(LeashDecisionRecord, decision_id)
            if ask is None:
                raise LeashError(
                    LeashErrorCode.LEASH_DECISION_NOT_FOUND,
                    "Leash decision not found.",
                    {"decision_id": decision_id},
                )
            action = GuardAction(ask.action)
            subject_id = ask.subject_id
            claim = db.execute(
                update(LeashDecisionRecord)
                .where(LeashDecisionRecord.id == decision_id, LeashDecisionRecord.status == "OPEN")
                .values(status="APPROVED" if approve else "REJECTED", resolved_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            db.expire(ask)
            if cast("CursorResult[Any]", claim).rowcount == 0:
                raise LeashError(
                    LeashErrorCode.LEASH_ALREADY_RESOLVED,
                    "This Leash decision was already resolved.",
                    {"decision_id": decision_id, "status": ask.status},
                )
            rule_ids = list(ask.rule_ids_json)
            organiser_row = LeashDecisionRecord(
                id=new_id("lsd"),
                circle_id=ask.circle_id,
                policy_version=ask.policy_version,
                action=ask.action,
                subject_type=ask.subject_type,
                subject_id=subject_id,
                decision=(LeashDecision.ALLOW if approve else LeashDecision.DENY).value,
                rule_ids_json=rule_ids,
                facts_json=ask.facts_json,
                amount=ask.amount,
                reason=_organiser_reason(approve, rule_ids, note),
                actor="organiser",
                status="FINAL",
                approves_id=decision_id,
            )
            db.add(organiser_row)
            db.flush()
            result: dict[str, Any] | None = None
            if approve:
                result = get_engine().resume(
                    db, action, subject_id, approved_decision_id=decision_id
                )
            else:
                get_engine().cancel(db, action, subject_id, decision_id)
            return LeashResolveResponse(
                decision_id=organiser_row.id,
                approves=decision_id,
                actor="organiser",
                result=result,
            )
    except BaseException:
        # The claim UPDATE bypassed the ORM, so a rolled-back SAVEPOINT leaves the loaded row stale.
        stale = db.identity_map.get(identity_key(LeashDecisionRecord, decision_id))
        if stale is not None:
            db.expire(stale)
        raise
