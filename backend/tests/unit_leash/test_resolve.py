"""Service-level tests for resolve(): idempotent approve/reject with engine resume."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from app.leash import service
from app.leash.facts import BaseFacts, ChaseInvoiceFacts, PayoutFacts
from app.leash.models import LeashDecisionRecord
from app.leash.policy import save_policy
from app.leash.service import LeashError, configure, decide, resolve
from app.schemas.contracts import (
    GuardAction,
    LeashDecision,
    LeashErrorCode,
    LeashThresholds,
)
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .fakes import FakeEngine, FakeFactsProvider

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
PO = GuardAction.RELEASE_POT_PAYOUT


def amina_facts(payout_id: str = "pay_amina") -> PayoutFacts:
    return PayoutFacts.model_validate(
        {
            "now": NOW,
            "circle_id": "c1",
            "cycle_index": 2,
            "payout_id": payout_id,
            "amount": "300.00",
            "recipient_member_id": "m_amina",
            "recipient_name": "Amina",
            "recipient_payout_email": "amina@x.com",
            "payer_emails": ["amina@x.com", "b@x.com"],
            "reserve_cover_used": False,
            "recipient_is_organiser": False,
            "recipient_has_unpaid": False,
            "payout_email_changed_at": datetime(2026, 10, 27, 12, tzinfo=UTC),
        }
    )


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture(autouse=True)
def wired(engine: FakeEngine) -> Iterator[None]:
    facts: dict[str, tuple[str, BaseFacts]] = {
        "pay_amina": ("payout", amina_facts()),
        "pay_b": ("payout", amina_facts("pay_b")),
    }
    saved = (service._facts, service._engine)
    configure(FakeFactsProvider(facts), engine)
    yield
    service._facts, service._engine = saved


def rows(db: Session) -> list[LeashDecisionRecord]:
    return list(db.scalars(select(LeashDecisionRecord)))


def ask_id(db: Session, subject_id: str = "pay_amina") -> str:
    r = decide(db, PO, subject_id)
    assert r.decision is LeashDecision.ASK
    return r.decision_id


def swap_engine(engine: FakeEngine) -> None:
    configure(service.get_facts_provider(), engine)


def test_approve_resumes_engine_once(db: Session, engine: FakeEngine) -> None:
    ask = ask_id(db)
    before = db.get(LeashDecisionRecord, ask)
    assert before is not None
    cols = ("circle_id", "policy_version", "action", "subject_id", "decision", "reason")
    snapshot = {c: getattr(before, c) for c in cols}
    resp = resolve(db, ask, True, "org1")
    assert engine.resumed == [(PO, "pay_amina", ask)] and engine.cancelled == []
    assert resp.approves == ask and resp.actor == "organiser"
    assert resp.result == {"status": "resumed", "subject_id": "pay_amina"}
    orig = db.get(LeashDecisionRecord, ask)
    assert orig is not None and orig.status == "APPROVED" and orig.resolved_at is not None
    assert {c: getattr(orig, c) for c in cols} == snapshot
    org = db.get(LeashDecisionRecord, resp.decision_id)
    assert org is not None and org.approves_id == ask and org.actor == "organiser"
    assert org.decision == "ALLOW" and org.status == "FINAL" and org.rule_ids_json == ["PO-01"]
    assert org.reason == "Approved by organiser: PO-01."
    assert org.circle_id == "c1" and org.amount == orig.amount and org.facts_json == orig.facts_json


def test_note_appended_when_it_fits(db: Session) -> None:
    resp = resolve(db, ask_id(db), True, "org1", note="Confirmed by phone")
    org = db.get(LeashDecisionRecord, resp.decision_id)
    assert org is not None
    assert org.reason == "Approved by organiser: PO-01. Note: Confirmed by phone"


def test_long_note_is_dropped(db: Session) -> None:
    resp = resolve(db, ask_id(db), False, "org1", note="x" * 200)
    org = db.get(LeashDecisionRecord, resp.decision_id)
    assert org is not None and org.reason == "Rejected by organiser: PO-01."


def test_second_approve_409_and_engine_not_called_again(db: Session, engine: FakeEngine) -> None:
    ask = ask_id(db)
    resolve(db, ask, True, "org1")
    with pytest.raises(LeashError) as exc:
        resolve(db, ask, True, "org1")
    assert exc.value.code is LeashErrorCode.LEASH_ALREADY_RESOLVED and exc.value.http_status == 409
    assert exc.value.details == {"decision_id": ask, "status": "APPROVED"}
    assert len(engine.resumed) == 1
    assert len(rows(db)) == 2


def test_approve_after_reject_409(db: Session, engine: FakeEngine) -> None:
    ask = ask_id(db)
    resolve(db, ask, False, "org1")
    with pytest.raises(LeashError) as exc:
        resolve(db, ask, True, "org1")
    assert exc.value.details["status"] == "REJECTED" and engine.resumed == []


def test_final_row_cannot_be_resolved(db: Session) -> None:
    resp = resolve(db, ask_id(db), True, "org1")
    with pytest.raises(LeashError) as exc:
        resolve(db, resp.decision_id, False, "org1")
    assert exc.value.code is LeashErrorCode.LEASH_ALREADY_RESOLVED
    assert exc.value.details["status"] == "FINAL"


def test_reject_cancels_and_blocks(db: Session, engine: FakeEngine) -> None:
    ask = ask_id(db)
    resp = resolve(db, ask, False, "org1")
    assert engine.cancelled == [(PO, "pay_amina", ask)] and engine.resumed == []
    assert resp.result is None and resp.approves == ask
    orig = db.get(LeashDecisionRecord, ask)
    assert orig is not None and orig.status == "REJECTED" and orig.resolved_at is not None
    org = db.get(LeashDecisionRecord, resp.decision_id)
    assert org is not None and org.decision == "DENY" and org.approves_id == ask
    assert org.reason == "Rejected by organiser: PO-01."
    with pytest.raises(ValueError, match="already been used|not an approved decision"):
        decide(db, PO, "pay_amina", approved_decision_id=ask)


def test_unknown_id_404(db: Session, engine: FakeEngine) -> None:
    with pytest.raises(LeashError) as exc:
        resolve(db, "lsd_missing", True, "org1")
    assert exc.value.code is LeashErrorCode.LEASH_DECISION_NOT_FOUND
    assert exc.value.http_status == 404 and engine.resumed == []


def test_engine_failure_rolls_back(db: Session) -> None:
    failing = FakeEngine(raise_on_resume=RuntimeError("paypal down"))
    swap_engine(failing)
    ask = ask_id(db)
    with pytest.raises(RuntimeError):
        resolve(db, ask, True, "org1")
    orig = db.get(LeashDecisionRecord, ask)
    assert orig is not None and orig.status == "OPEN" and orig.resolved_at is None
    assert [r.id for r in rows(db)] == [ask]
    failing.raise_on_resume = None
    resp = resolve(db, ask, True, "org1")
    assert resp.approves == ask and len(rows(db)) == 2
    assert failing.resumed == [(PO, "pay_amina", ask)]


def test_resolve_does_not_commit(db: Session) -> None:
    ask = ask_id(db)
    db.commit()
    resolve(db, ask, True, "org1")
    db.rollback()
    orig = db.get(LeashDecisionRecord, ask)
    assert orig is not None and orig.status == "OPEN" and len(rows(db)) == 1


class ResumingEngine(FakeEngine):
    """Mimics the real engine: re-run decide() with the approval, then 'pay'."""

    def resume(
        self, db: Session, action: GuardAction, subject_id: str, approved_decision_id: str
    ) -> dict[str, Any]:
        outcome = decide(db, action, subject_id, approved_decision_id=approved_decision_id)
        self.resumed.append((action, subject_id, approved_decision_id))
        return {"decision": outcome.decision.value, "decision_id": outcome.decision_id}


class PayFailsEngine(ResumingEngine):
    def resume(
        self, db: Session, action: GuardAction, subject_id: str, approved_decision_id: str
    ) -> dict[str, Any]:
        super().resume(db, action, subject_id, approved_decision_id)
        raise RuntimeError("payment failed after decide")


def test_approve_resume_decide_end_to_end(db: Session) -> None:
    swap_engine(ResumingEngine())
    ask = ask_id(db)
    resp = resolve(db, ask, True, "org1")
    assert resp.result is not None and resp.result["decision"] == "ALLOW"
    final = db.get(LeashDecisionRecord, resp.result["decision_id"])
    assert final is not None and final.actor == "leash" and final.approves_id == ask
    assert final.status == "FINAL" and final.facts_json["approved_rule_ids"] == ["PO-01"]
    assert len(rows(db)) == 3
    # The approval is single-use: a second resume-path decide must fail.
    with pytest.raises(ValueError, match="already been used|not an approved decision"):
        decide(db, PO, "pay_amina", approved_decision_id=ask)


def test_end_to_end_failure_after_decide_leaves_approval_unused(db: Session) -> None:
    swap_engine(PayFailsEngine())
    ask = ask_id(db)
    with pytest.raises(RuntimeError):
        resolve(db, ask, True, "org1")
    assert [r.id for r in rows(db)] == [ask]
    swap_engine(ResumingEngine())
    assert resolve(db, ask, True, "org1").result is not None


_SET = re.compile(r"^\s*UPDATE\s+leash_decisions\s+SET\s+(.*?)\s+WHERE", re.IGNORECASE | re.DOTALL)


def test_append_only(db: Session) -> None:
    statements: list[str] = []
    orm_touched: list[tuple[str, str]] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _capture(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        verb = statement.lstrip().split(None, 1)[0].upper()
        if "leash_decisions" in statement and verb in {"UPDATE", "DELETE"}:
            statements.append(statement)

    @event.listens_for(db, "before_flush")
    def _flush(session: Session, *_a: Any) -> None:
        for obj in session.dirty:
            if isinstance(obj, LeashDecisionRecord) and session.is_modified(obj):
                orm_touched.append(("UPDATE", obj.id))
        for obj in session.deleted:
            if isinstance(obj, LeashDecisionRecord):
                orm_touched.append(("DELETE", obj.id))

    resolve(db, ask_id(db, "pay_amina"), True, "org1")
    resolve(db, ask_id(db, "pay_b"), False, "org1")

    assert orm_touched == []
    assert len(statements) == 2
    for stmt in statements:
        m = _SET.match(stmt)
        assert m is not None, stmt
        cols = {part.split("=")[0].strip() for part in m.group(1).split(",")}
        assert cols == {"status", "resolved_at"}, stmt


CH = GuardAction.SEND_CHASE_INVOICE


def chase_facts(**kw: Any) -> ChaseInvoiceFacts:
    data: dict[str, Any] = {
        "now": NOW,
        "circle_id": "c1",
        "member_id": "m2",
        "member_name": "Omar",
        "invoice_amount": "100.00",
        "unpaid_amount": "100.00",
        "due_at": datetime(2026, 10, 20, tzinfo=UTC),
        "open_invoice_count": 0,
        "no_auto_chase": False,
    }
    return ChaseInvoiceFacts.model_validate(data | kw)


def payout_facts_with(**kw: Any) -> PayoutFacts:
    return PayoutFacts.model_validate(amina_facts().model_dump() | kw)


def approve_until_allowed(db: Session, action: GuardAction, subject_id: str) -> list[list[str]]:
    """Approve each new ASK in turn; return the rule_ids of every ASK seen."""
    seen: list[list[str]] = []
    outcome = decide(db, action, subject_id)
    for _ in range(4):
        if outcome.decision is not LeashDecision.ASK:
            break
        seen.append(outcome.rule_ids)
        resp = resolve(db, outcome.decision_id, True, "org1")
        assert resp.result is not None
        outcome = decide_result(db, resp.result["decision_id"])
    assert outcome.decision is LeashDecision.ALLOW
    return seen


def decide_result(db: Session, decision_id: str) -> Any:
    row = db.get(LeashDecisionRecord, decision_id)
    assert row is not None
    return service._result(row)


def set_facts(facts_map: dict[str, tuple[str, BaseFacts]]) -> None:
    configure(FakeFactsProvider(facts_map), ResumingEngine())


def test_strict_reserve_cover_terminates(db: Session) -> None:
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    set_facts(
        {"p": ("payout", payout_facts_with(reserve_cover_used=True, payout_email_changed_at=None))}
    )
    seen = approve_until_allowed(db, PO, "p")
    assert seen == [["PO-03"], ["PO-00", "STRICT"]]


def test_strict_recently_changed_email_terminates(db: Session) -> None:
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    set_facts({"p": ("payout", amina_facts())})
    seen = approve_until_allowed(db, PO, "p")
    assert seen == [["PO-01"], ["PO-00", "STRICT"]]


def test_strict_organiser_recipient_terminates(db: Session) -> None:
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    set_facts(
        {
            "p": (
                "payout",
                payout_facts_with(recipient_is_organiser=True, payout_email_changed_at=None),
            )
        }
    )
    assert approve_until_allowed(db, PO, "p") == [["PO-05"], ["PO-00", "STRICT"]]


def test_standard_chase_not_yet_due_terminates(db: Session) -> None:
    set_facts(
        {
            "inv": (
                "chase_plan",
                chase_facts(no_auto_chase=True, due_at=datetime(2026, 10, 28, tzinfo=UTC)),
            )
        }
    )
    assert approve_until_allowed(db, CH, "inv") == [["G-03"], ["DEFAULT-ASK"]]


def test_chain_waivers_are_union_of_the_chain(db: Session) -> None:
    save_policy(db, "c1", "STRICT", LeashThresholds(), [])
    set_facts(
        {"p": ("payout", payout_facts_with(reserve_cover_used=True, payout_email_changed_at=None))}
    )
    first = decide(db, PO, "p")
    r1 = resolve(db, first.decision_id, True, "org1")
    assert r1.result is not None
    second_id = r1.result["decision_id"]
    r2 = resolve(db, second_id, True, "org1")
    assert r2.result is not None
    final = db.get(LeashDecisionRecord, r2.result["decision_id"])
    assert final is not None and final.facts_json["approved_rule_ids"] == [
        "PO-00",
        "STRICT",
        "PO-03",
    ]


def test_chain_deeper_than_ten_rejected(db: Session) -> None:
    prev: str | None = None
    for i in range(12):
        row_id = f"chain{i:02d}"
        db.add(
            LeashDecisionRecord(
                id=row_id,
                circle_id="c1",
                policy_version=1,
                action=PO.value,
                subject_type="payout",
                subject_id="pay_amina",
                decision="ASK",
                rule_ids_json=["PO-01"],
                facts_json={},
                amount=None,
                reason="x",
                actor="leash",
                status="APPROVED",
                approves_id=prev,
            )
        )
        db.flush()
        prev = row_id
    with pytest.raises(ValueError, match="chain"):
        decide(db, PO, "pay_amina", approved_decision_id="chain11")


def test_chain_row_for_other_subject_rejected(db: Session) -> None:
    for rid, subj, prev in (("a1", "other", None), ("a2", "pay_amina", "a1")):
        db.add(
            LeashDecisionRecord(
                id=rid,
                circle_id="c1",
                policy_version=1,
                action=PO.value,
                subject_type="payout",
                subject_id=subj,
                decision="ASK",
                rule_ids_json=["PO-01"],
                facts_json={},
                amount=None,
                reason="x",
                actor="leash",
                status="APPROVED",
                approves_id=prev,
            )
        )
    db.flush()
    with pytest.raises(ValueError, match="chain"):
        decide(db, PO, "pay_amina", approved_decision_id="a2")
