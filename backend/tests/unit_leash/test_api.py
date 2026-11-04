"""REST-level tests for the Leash router, on a throwaway FastAPI app."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from app.api import leash as api
from app.api.leash import Actor, get_actor, get_db, register_leash_errors
from app.leash import LeashError, configure, decide, service
from app.leash.facts import BaseFacts, PayoutFacts
from app.leash.models import LeashDecisionRecord
from app.schemas.contracts import GuardAction, LeashErrorCode
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .fakes import FakeEngine, FakeFactsProvider

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
PO = GuardAction.RELEASE_POT_PAYOUT


def make_client(db: Session, actor: Actor) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    register_leash_errors(app)

    def _db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_actor] = lambda: actor
    return TestClient(app)


ORGANISER = Actor(user_id="org1", organiser_of=frozenset({"c1"}))
MEMBER = Actor(user_id="m1", organiser_of=frozenset())


def test_placeholders_raise() -> None:
    with pytest.raises(NotImplementedError):
        next(get_db())
    with pytest.raises(NotImplementedError):
        get_actor()


def test_error_envelope_uses_request_id_header(db: Session) -> None:
    client = make_client(db, ORGANISER)

    @client.app.get("/boom")  # type: ignore[attr-defined]
    def boom() -> None:
        raise LeashError(LeashErrorCode.LEASH_DISABLED, "off", {"k": 1})

    r = client.get("/boom", headers={"X-Request-ID": "abc"})
    assert r.status_code == 503
    assert r.json() == {
        "error": {
            "code": "LEASH_DISABLED",
            "message": "off",
            "details": {"k": 1},
            "request_id": "abc",
        }
    }


def test_error_envelope_generates_request_id(db: Session) -> None:
    client = make_client(db, ORGANISER)

    @client.app.get("/boom")  # type: ignore[attr-defined]
    def boom() -> None:
        raise LeashError(LeashErrorCode.ORGANISER_ONLY, "no")

    r = client.get("/boom")
    assert r.status_code == 401
    assert re.fullmatch(r"req_[0-9A-Z]{26}", r.json()["error"]["request_id"])
    assert r.json()["error"]["details"] == {}


def test_get_policy_default_v1(db: Session) -> None:
    r = make_client(db, MEMBER).get("/api/v1/circles/c1/leash/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["circle_id"] == "c1" and body["version"] == 1 and body["preset"] == "STANDARD"
    assert body["thresholds"]["reminder_min_gap_hours"] == 48
    assert body["no_auto_chase_member_ids"] == []
    # Lazily created v1 was committed, so it survives a rollback.
    db.rollback()
    assert make_client(db, MEMBER).get("/api/v1/circles/c1/leash/policy").json()["version"] == 1


def test_put_policy_bumps_version(db: Session) -> None:
    client = make_client(db, ORGANISER)
    client.get("/api/v1/circles/c1/leash/policy")
    r = client.put(
        "/api/v1/circles/c1/leash/policy",
        json={
            "circle_id": "ignored",
            "version": 99,
            "preset": "STRICT",
            "thresholds": {"reminder_min_gap_hours": 72},
            "no_auto_chase_member_ids": ["m_x"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2 and body["circle_id"] == "c1" and body["preset"] == "STRICT"
    assert body["thresholds"]["reminder_min_gap_hours"] == 72
    assert body["no_auto_chase_member_ids"] == ["m_x"]
    db.rollback()
    assert client.get("/api/v1/circles/c1/leash/policy").json()["version"] == 2


def test_put_policy_member_401_organiser_only(db: Session) -> None:
    r = make_client(db, MEMBER).put("/api/v1/circles/c1/leash/policy", json={"preset": "STRICT"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "ORGANISER_ONLY"


def test_put_policy_organiser_of_other_circle_401(db: Session) -> None:
    other = Actor(user_id="o2", organiser_of=frozenset({"c2"}))
    r = make_client(db, other).put("/api/v1/circles/c1/leash/policy", json={})
    assert r.status_code == 401


def test_put_policy_gap_11h_422_policy_invalid(db: Session) -> None:
    r = make_client(db, ORGANISER).put(
        "/api/v1/circles/c1/leash/policy",
        json={"preset": "STANDARD", "thresholds": {"reminder_min_gap_hours": 11}},
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "LEASH_POLICY_INVALID"
    assert isinstance(err["details"], dict)
    first = err["details"]["errors"][0]
    assert first["loc"] == ["thresholds", "reminder_min_gap_hours"]
    assert isinstance(first["msg"], str)


def test_put_policy_malformed_json_422(db: Session) -> None:
    r = make_client(db, ORGANISER).put(
        "/api/v1/circles/c1/leash/policy",
        content=b"{nope",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "LEASH_POLICY_INVALID"
    assert r.json()["error"]["details"]["errors"][0]["loc"] == []


def payout_facts(payout_id: str, name: str = "Amina") -> PayoutFacts:
    return PayoutFacts.model_validate(
        {
            "now": NOW,
            "circle_id": "c1",
            "cycle_index": 2,
            "payout_id": payout_id,
            "amount": "600.00",
            "recipient_member_id": "m_amina",
            "recipient_name": name,
            "recipient_payout_email": "amina.new@personal.example.com",
            "payer_emails": ["amina.new@personal.example.com", "b@x.com"],
            "reserve_cover_used": False,
            "recipient_is_organiser": False,
            "recipient_has_unpaid": False,
            "payout_email_changed_at": datetime(2026, 10, 27, 12, tzinfo=UTC),
        }
    )


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def wired(engine: FakeEngine) -> Iterator[None]:
    facts: dict[str, tuple[str, BaseFacts]] = {
        "pay_amina": ("payout", payout_facts("pay_amina")),
        "pay_b": ("payout", payout_facts("pay_b", "Bea")),
    }
    saved = (service._facts, service._engine)
    configure(FakeFactsProvider(facts), engine)
    yield
    service._facts, service._engine = saved


@pytest.mark.usefixtures("wired")
def test_inbox_shows_amina_held_payout(db: Session) -> None:
    ask = decide(db, PO, "pay_amina")
    db.commit()
    r = make_client(db, ORGANISER).get("/api/v1/circles/c1/leash/inbox")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["decision_id"] == ask.decision_id
    assert item["rule_ids"] == ["PO-01"] and item["amount"] == "600.00"
    assert item["recipient"] == {
        "member_id": "m_amina",
        "name": "Amina",
        "payout_email": "amina.new@personal.example.com",
    }
    assert item["subject_type"] == "payout" and item["cycle_index"] == 2
    assert item["created_at"].endswith(("Z", "+00:00"))


@pytest.mark.usefixtures("wired")
def test_inbox_member_401_and_other_circle_empty(db: Session) -> None:
    decide(db, PO, "pay_amina")
    db.commit()
    assert make_client(db, MEMBER).get("/api/v1/circles/c1/leash/inbox").status_code == 401
    other = Actor(user_id="o2", organiser_of=frozenset({"c2"}))
    assert make_client(db, other).get("/api/v1/circles/c2/leash/inbox").json() == {"items": []}


def test_inbox_503_when_disabled(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEASH_ENABLED", "false")
    r = make_client(db, ORGANISER).get("/api/v1/circles/c1/leash/inbox")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "LEASH_DISABLED"


@pytest.mark.usefixtures("wired")
def test_inbox_oldest_first(db: Session) -> None:
    first = decide(db, PO, "pay_amina")
    second = decide(db, PO, "pay_b")
    db.commit()
    items = make_client(db, ORGANISER).get("/api/v1/circles/c1/leash/inbox").json()["items"]
    assert [i["decision_id"] for i in items] == [first.decision_id, second.decision_id]


@pytest.mark.usefixtures("wired")
def test_decisions_newest_first(db: Session) -> None:
    first = decide(db, PO, "pay_amina")
    second = decide(db, PO, "pay_b")
    db.commit()
    client = make_client(db, ORGANISER)
    r = client.get("/api/v1/circles/c1/leash/decisions")
    assert r.status_code == 200
    rows = r.json()
    assert [x["decision_id"] for x in rows] == [second.decision_id, first.decision_id]
    assert rows[0]["status"] == "OPEN" and rows[0]["amount"] == "600.00"
    assert len(client.get("/api/v1/circles/c1/leash/decisions?limit=1").json()) == 1
    assert make_client(db, MEMBER).get("/api/v1/circles/c1/leash/decisions").status_code == 401


@pytest.mark.usefixtures("wired")
def test_approve_then_approve_again_409(db: Session, engine: FakeEngine) -> None:
    did = decide(db, PO, "pay_amina").decision_id
    db.commit()
    client = make_client(db, ORGANISER)
    first = client.post(f"/api/v1/leash/decisions/{did}/approve", json={"note": "ok"})
    assert first.status_code == 200
    body = first.json()
    assert body["approves"] == did and body["actor"] == "organiser"
    assert body["result"] == {"status": "resumed", "subject_id": "pay_amina"}
    second = client.post(f"/api/v1/leash/decisions/{did}/approve", json={})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "LEASH_ALREADY_RESOLVED"
    assert len(engine.resumed) == 1
    db.rollback()  # the approval was committed
    assert client.get("/api/v1/circles/c1/leash/inbox").json() == {"items": []}


@pytest.mark.usefixtures("wired")
def test_reject_returns_200_and_inbox_empty(db: Session, engine: FakeEngine) -> None:
    did = decide(db, PO, "pay_amina").decision_id
    db.commit()
    client = make_client(db, ORGANISER)
    r = client.post(f"/api/v1/leash/decisions/{did}/reject")  # no body at all
    assert r.status_code == 200
    assert r.json()["approves"] == did and r.json()["result"] is None
    assert engine.cancelled == [(PO, "pay_amina", did)]
    assert client.get("/api/v1/circles/c1/leash/inbox").json() == {"items": []}
    assert client.get("/api/v1/circles/c1/leash/decisions").json()[0]["actor"] == "organiser"


@pytest.mark.usefixtures("wired")
def test_resolve_errors_404_and_401(db: Session, engine: FakeEngine) -> None:
    did = decide(db, PO, "pay_amina").decision_id
    db.commit()
    missing = make_client(db, ORGANISER).post("/api/v1/leash/decisions/lsd_nope/approve")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "LEASH_DECISION_NOT_FOUND"
    for verb in ("approve", "reject"):
        denied = make_client(db, MEMBER).post(f"/api/v1/leash/decisions/{did}/{verb}")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "ORGANISER_ONLY"
    assert engine.resumed == [] and engine.cancelled == []


@pytest.mark.usefixtures("wired")
def test_engine_failure_rolls_back_and_propagates(db: Session) -> None:
    configure(service.get_facts_provider(), FakeEngine(raise_on_resume=RuntimeError("down")))
    did = decide(db, PO, "pay_amina").decision_id
    db.commit()
    client = make_client(db, ORGANISER)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/leash/decisions/{did}/approve")
    assert len(client.get("/api/v1/circles/c1/leash/inbox").json()["items"]) == 1


@pytest.mark.usefixtures("wired")
def test_organiser_of_other_circle_cannot_resolve_or_read(db: Session, engine: FakeEngine) -> None:
    did = decide(db, PO, "pay_amina").decision_id  # circle c1
    db.commit()
    other = Actor(user_id="o2", organiser_of=frozenset({"c2"}))
    client = make_client(db, other)
    for verb in ("approve", "reject"):
        denied = client.post(f"/api/v1/leash/decisions/{did}/{verb}")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "ORGANISER_ONLY"
    assert client.get("/api/v1/circles/c1/leash/inbox").status_code == 401
    assert engine.resumed == [] and engine.cancelled == []
    row = db.get(LeashDecisionRecord, did)
    assert row is not None and row.status == "OPEN"
    assert (
        len(make_client(db, ORGANISER).get("/api/v1/circles/c1/leash/inbox").json()["items"]) == 1
    )
