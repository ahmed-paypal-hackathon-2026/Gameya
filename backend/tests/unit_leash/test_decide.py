"""Service-level tests for decide(): facts -> rules -> reason -> decision log."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.leash import service
from app.leash.facts import BaseFacts, ChaseInvoiceFacts, PayoutFacts
from app.leash.models import LeashDecisionRecord
from app.leash.policy import save_policy
from app.leash.service import configure, decide
from app.schemas.contracts import GuardAction, LeashDecision, LeashThresholds
from sqlalchemy import select
from sqlalchemy.orm import Session

from .fakes import FakeEngine, FakeFactsProvider

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
PO = GuardAction.RELEASE_POT_PAYOUT
CH = GuardAction.SEND_CHASE_INVOICE


def payout(name: str, email: str, **kw: object) -> PayoutFacts:
    data: dict[str, object] = {
        "now": NOW,
        "circle_id": "c1",
        "cycle_index": 2,
        "payout_id": f"pay_{name}",
        "amount": "300.00",
        "recipient_member_id": f"m_{name}",
        "recipient_name": name,
        "recipient_payout_email": email,
        "payer_emails": [email, "b@x.com"],
        "reserve_cover_used": False,
        "recipient_is_organiser": False,
        "recipient_has_unpaid": False,
    }
    return PayoutFacts.model_validate(data | kw)


def chase(**kw: object) -> ChaseInvoiceFacts:
    data: dict[str, object] = {
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


@pytest.fixture
def facts() -> dict[str, tuple[str, BaseFacts]]:
    return {
        "pay_sara": ("payout", payout("Sara", "sara@x.com")),
        "pay_amina": (
            "payout",
            payout(
                "Amina",
                "amina@x.com",
                payout_email_changed_at=datetime(2026, 10, 27, 12, tzinfo=UTC),
            ),
        ),
        "inv_dup": ("chase_plan", chase(open_invoice_count=1)),
    }


@pytest.fixture(autouse=True)
def wired(facts: dict[str, tuple[str, BaseFacts]]) -> Iterator[None]:
    saved = (service._facts, service._engine)
    configure(FakeFactsProvider(facts), FakeEngine())
    yield
    service._facts, service._engine = saved


def rows(db: Session) -> list[LeashDecisionRecord]:
    return list(db.scalars(select(LeashDecisionRecord)))


def test_clean_payout_allows_and_logs_final_row(db: Session) -> None:
    r = decide(db, PO, "pay_sara")
    assert r.decision is LeashDecision.ALLOW and r.rule_ids == ["PO-00"]
    (row,) = rows(db)
    assert row.status == "FINAL" and row.policy_version == 1 and row.actor == "leash"
    assert row.id == r.decision_id and row.amount == Decimal("300.00")
    assert row.circle_id == "c1" and row.subject_type == "payout"


def test_amina_email_changed_yesterday_asks_po01(db: Session) -> None:
    r = decide(db, PO, "pay_amina")
    assert r.decision is LeashDecision.ASK and r.rule_ids == ["PO-01"]
    assert r.reason.startswith("Held: Amina's payout email changed yesterday")
    (row,) = rows(db)
    assert row.status == "OPEN" and row.reason == r.reason


def test_duplicate_invoice_denied_g02(db: Session) -> None:
    r = decide(db, CH, "inv_dup")
    assert r.decision is LeashDecision.DENY and r.rule_ids == ["G-02"]
    (row,) = rows(db)
    assert row.status == "FINAL" and row.amount == Decimal("100.00")


def test_repeat_decide_returns_existing_open(db: Session) -> None:
    first = decide(db, PO, "pay_amina")
    second = decide(db, PO, "pay_amina")
    assert second.decision_id == first.decision_id
    assert second.decision is LeashDecision.ASK and second.reason == first.reason
    assert len(rows(db)) == 1


def test_approved_decision_waives_rules(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    ask_row = rows(db)[0]
    ask_row.status = "APPROVED"
    db.flush()
    r = decide(db, PO, "pay_amina", approved_decision_id=ask.decision_id)
    assert r.decision is LeashDecision.ALLOW and r.decision_id != ask.decision_id
    new = db.get(LeashDecisionRecord, r.decision_id)
    assert new is not None and new.approves_id == ask.decision_id and new.status == "FINAL"
    assert new.facts_json["approved_rule_ids"] == ["PO-01"]


@pytest.mark.parametrize("status", ["OPEN", "REJECTED", "FINAL"])
def test_approval_must_be_approved_row(db: Session, status: str) -> None:
    ask = decide(db, PO, "pay_amina")
    rows(db)[0].status = status
    db.flush()
    with pytest.raises(ValueError):
        decide(db, PO, "pay_amina", approved_decision_id=ask.decision_id)


def test_approval_must_match_subject_and_exist(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    rows(db)[0].status = "APPROVED"
    db.flush()
    with pytest.raises(ValueError):
        decide(db, PO, "pay_sara", approved_decision_id=ask.decision_id)
    with pytest.raises(ValueError):
        decide(db, PO, "pay_amina", approved_decision_id="lsd_missing")


def test_leash_disabled_allows_and_logs(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEASH_ENABLED", "false")
    r = decide(db, PO, "pay_amina")
    assert r.decision is LeashDecision.ALLOW and r.rule_ids == ["LEASH_DISABLED"]
    assert r.reason == "Allowed: Leash is off."
    (row,) = rows(db)
    assert row.status == "FINAL" and row.policy_version == 1 and row.circle_id == "c1"
    assert row.amount == Decimal("300.00")


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "No"])
def test_leash_enabled_off_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LEASH_ENABLED", value)
    assert service.leash_enabled() is False


def test_leash_enabled_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEASH_ENABLED", raising=False)
    assert service.leash_enabled() is True


def test_decision_records_policy_version_after_save(db: Session) -> None:
    save_policy(db, "c1", "STANDARD", LeashThresholds(), [])
    save_policy(db, "c1", "STANDARD", LeashThresholds(), [])
    decide(db, PO, "pay_sara")
    assert rows(db)[0].policy_version == 2


def test_decide_does_not_commit(db: Session) -> None:
    decide(db, PO, "pay_sara")
    db.rollback()
    assert rows(db) == []


def test_unconfigured_raises(db: Session) -> None:
    service._facts = None
    with pytest.raises(RuntimeError):
        decide(db, PO, "pay_sara")


def test_approval_is_single_use(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    rows(db)[0].status = "APPROVED"
    db.flush()
    first = decide(db, PO, "pay_amina", approved_decision_id=ask.decision_id)
    assert first.decision is LeashDecision.ALLOW
    with pytest.raises(ValueError, match="already been used"):
        decide(db, PO, "pay_amina", approved_decision_id=ask.decision_id)
    assert len(rows(db)) == 2


def test_organiser_row_does_not_count_as_a_use(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    ask_row = rows(db)[0]
    ask_row.status = "APPROVED"
    db.add(
        LeashDecisionRecord(
            id="lsd_org",
            circle_id="c1",
            policy_version=1,
            action=PO.value,
            subject_type="payout",
            subject_id="pay_amina",
            decision="ALLOW",
            rule_ids_json=["PO-01"],
            facts_json={},
            reason="Approved by organiser: PO-01.",
            actor="organiser",
            status="FINAL",
            approves_id=ask.decision_id,
        )
    )
    db.flush()
    r = decide(db, PO, "pay_amina", approved_decision_id=ask.decision_id)
    assert r.decision is LeashDecision.ALLOW


def test_approval_must_match_action(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    rows(db)[0].status = "APPROVED"
    db.flush()
    with pytest.raises(ValueError, match="not an approved decision"):
        decide(db, CH, "pay_amina", approved_decision_id=ask.decision_id)


def test_open_row_for_other_subject_does_not_dedupe(db: Session) -> None:
    amina = decide(db, PO, "pay_amina")
    sara = decide(db, PO, "pay_sara")
    assert sara.decision_id != amina.decision_id and sara.decision is LeashDecision.ALLOW
    assert len(rows(db)) == 2


def test_open_row_for_other_action_does_not_dedupe(db: Session) -> None:
    # Same subject_id, different action: only the action filter can tell them apart.
    configure(
        FakeFactsProvider(
            {
                "shared": (
                    "payout",
                    payout(
                        "Amina",
                        "amina@x.com",
                        payout_email_changed_at=datetime(2026, 10, 27, 12, tzinfo=UTC),
                    ),
                ),
                (CH, "shared"): ("chase_plan", chase()),
            }
        ),
        FakeEngine(),
    )
    ask = decide(db, PO, "shared")
    assert ask.decision is LeashDecision.ASK
    other = decide(db, CH, "shared")
    assert other.decision_id != ask.decision_id
    assert {r.action for r in rows(db)} == {PO.value, CH.value}
    assert len(rows(db)) == 2


def test_unknown_subject_type_rejected_and_nothing_stored(db: Session) -> None:
    configure(FakeFactsProvider({"bad": ("invoice", chase())}), FakeEngine())
    with pytest.raises(ValueError, match="subject_type"):
        decide(db, CH, "bad")
    assert rows(db) == []
