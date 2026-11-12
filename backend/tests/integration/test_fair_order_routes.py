"""Task 6: needs, fair order, stress-test and reliability are served by the engine."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.ai.fair_order import derive_seed  # noqa: E402
from app.api import circles as circles_api  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import Membership, OrderApproval  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.main import create_app  # noqa: E402
from app.ml.stress_test import run_stress_test  # noqa: E402
from app.schemas.contracts import MemberId, ReliabilityScore  # noqa: E402
from app.services.circle_data import circle_snapshot, reliability_scores  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

CIRCLE = f"/api/v1/circles/{GOLDEN_CIRCLE_ID}"
GOLDEN_NEEDS: dict[str, tuple[str, str]] = {
    "amina": ("My mother needs surgery in January, it's urgent", "en"),
    "omar": ("عندي فرح في مارس ومحتاج حوالي ٦٠٠ دولار", "ar-EG"),
    "sara": ("I need money for my kids' school fees in December, about $600", "en"),
    "youssef": ("عايز أجدد الشقة في الصيف، مش مستعجل", "ar-EG"),
    "lina": ("Saving for a trip to Turkey next summer, I'm flexible", "en"),
    "karim": ("محتاج بضاعة للمحل في فبراير", "ar-EG"),
}


def _session() -> Any:
    return next(get_db())


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # deterministic engine path
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        session = _session()
        try:
            seed_golden_circle(session)
            session.commit()
        finally:
            session.close()
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, name: str) -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": f"persona-{name}"})
    return {"Authorization": f"Bearer {token.json()['token']}"}


def _submit(client: TestClient, name: str) -> Any:
    text, locale = GOLDEN_NEEDS[name]
    return client.post(
        f"{CIRCLE}/needs",
        json={"member_id": f"mem-{name}", "raw_text": text, "locale": locale},
        headers=_bearer(client, name),
    )


def test_need_is_extracted_and_stored(client: TestClient) -> None:
    resp = _submit(client, "sara")
    assert resp.status_code == 200, resp.text
    need = resp.json()
    assert (need["member_id"], need["category"], need["target_date"]) == (
        "mem-sara",
        "EDUCATION",
        "2026-12-01",
    )
    listed = client.get(f"{CIRCLE}/needs", headers=_bearer(client, "omar"))
    assert listed.status_code == 200, listed.text
    assert [n["member_id"] for n in listed.json()] == ["mem-sara"]


def test_a_member_cannot_submit_someone_elses_need(client: TestClient) -> None:
    text, locale = GOLDEN_NEEDS["sara"]
    resp = client.post(
        f"{CIRCLE}/needs",
        json={"member_id": "mem-sara", "raw_text": text, "locale": locale},
        headers=_bearer(client, "omar"),
    )
    assert resp.status_code == 403
    assert client.post(f"{CIRCLE}/needs", json={}).status_code in (401, 422)


def test_order_uses_derive_seed_and_the_golden_order(client: TestClient) -> None:
    for name in GOLDEN_NEEDS:
        assert _submit(client, name).status_code == 200
    resp = client.post(
        f"{CIRCLE}/order/propose", json={"seed": 7}, headers=_bearer(client, "amina")
    )
    assert resp.status_code == 200, resp.text
    proposal = resp.json()
    order = [a["member_id"] for a in proposal["assignments"]]
    assert order[0] == "mem-amina" and order[-1] == "mem-lina"
    assert order.index("mem-omar") >= 2  # high risk stays out of the early slots
    members = [MemberId(f"mem-{n}") for n in GOLDEN_NEEDS]
    assert proposal["seed"] == derive_seed(GOLDEN_CIRCLE_ID, members) != 7
    assert set(proposal["explanations"]) == set(order)
    assert "reliability" not in str(proposal["explanations"]).lower()


def test_order_approval_is_recorded_once_per_member(client: TestClient) -> None:
    client.post(f"{CIRCLE}/order/propose", json={}, headers=_bearer(client, "amina"))
    for _ in range(2):
        resp = client.post(
            f"{CIRCLE}/order/approve",
            json={"member_id": "mem-sara"},
            headers=_bearer(client, "sara"),
        )
        assert resp.status_code == 200, resp.text
    session = _session()
    try:
        rows = session.scalars(
            select(OrderApproval).where(OrderApproval.circle_id == GOLDEN_CIRCLE_ID)
        ).all()
        assert [r.member_id for r in rows] == ["mem-sara"]
    finally:
        session.close()
    wrong = client.post(
        f"{CIRCLE}/order/approve", json={"member_id": "mem-sara"}, headers=_bearer(client, "omar")
    )
    assert wrong.status_code == 403

    for name in GOLDEN_NEEDS:
        client.post(
            f"{CIRCLE}/order/approve",
            json={"member_id": f"mem-{name}"},
            headers=_bearer(client, name),
        )
    detail = client.get(CIRCLE).json()
    assert sorted(m["slot_index"] for m in detail["members"]) == [0, 1, 2, 3, 4, 5]
    assert all(c["recipient_id"] for c in detail["cycles"])


def test_stress_test_matches_the_engine(client: TestClient) -> None:
    resp = client.post(
        f"{CIRCLE}/stress-test", json={"n_sims": 2000}, headers=_bearer(client, "amina")
    )
    assert resp.status_code == 200, resp.text
    session = _session()
    try:
        snapshot = circle_snapshot(session, GOLDEN_CIRCLE_ID)
        scores = list(reliability_scores(session, GOLDEN_CIRCLE_ID).values())
    finally:
        session.close()
    assert snapshot is not None
    expected = run_stress_test(snapshot, scores, [Decimal(i) for i in range(11)], n_sims=2000)
    body = resp.json()
    assert Decimal(body["recommended_reserve_pct"]) == expected.recommended_reserve_pct
    assert body["explanation"]


def test_stress_test_of_a_reliable_circle_recommends_three_percent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reliable(_session: Any, _circle_id: str) -> dict[MemberId, ReliabilityScore]:
        return {
            MemberId(f"mem-{n}"): ReliabilityScore(member_id=MemberId(f"mem-{n}"), p_late=0.03)
            for n in GOLDEN_NEEDS
        }

    monkeypatch.setattr(circles_api, "reliability_scores", reliable)
    resp = client.post(f"{CIRCLE}/stress-test", json={}, headers=_bearer(client, "amina"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["recommended_reserve_pct"]) == Decimal("3")
    assert "$18.00" in body["explanation"]


def test_stress_test_is_organiser_only(client: TestClient) -> None:
    resp = client.post(f"{CIRCLE}/stress-test", json={}, headers=_bearer(client, "omar"))
    assert resp.status_code in (401, 403)


def test_reliability_rows_hide_nothing_from_the_organiser(client: TestClient) -> None:
    resp = client.get(f"{CIRCLE}/reliability", headers=_bearer(client, "amina"))
    assert resp.status_code == 200, resp.text
    rows = {r["member_id"]: r for r in resp.json()["rows"]}
    assert len(rows) == 6
    assert rows["mem-omar"]["score"]["p_late"] > 0.5
    assert rows["mem-omar"]["display_name"] == "Omar"


def test_routes_no_longer_serve_fixtures(client: TestClient) -> None:
    amina = _bearer(client, "amina")
    responses = [
        _submit(client, "sara"),
        client.get(f"{CIRCLE}/needs", headers=amina),
        client.post(f"{CIRCLE}/order/propose", json={}, headers=amina),
        client.post(f"{CIRCLE}/stress-test", json={"n_sims": 500}, headers=amina),
        client.get(f"{CIRCLE}/reliability", headers=amina),
        client.get(CIRCLE),
    ]
    for resp in responses:
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("X-Demo-Fixture") is None, resp.url


def test_slots_follow_the_cycle_dates(client: TestClient) -> None:
    resp = client.post(f"{CIRCLE}/order/propose", json={}, headers=_bearer(client, "amina"))
    dates = [a["payout_date"] for a in resp.json()["assignments"]]
    assert dates[0] == date(2026, 11, 1).isoformat() and len(dates) == 6
    session = _session()
    try:
        assert session.scalars(
            select(Membership).where(Membership.circle_id == GOLDEN_CIRCLE_ID)
        ).all()
    finally:
        session.close()


def test_demo_mode_seeds_the_golden_circle_on_a_fresh_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("DEMO_MODE", "1")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            resp = c.get(f"{CIRCLE}/reliability", headers=_bearer(c, "amina"))
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["rows"]) == 6
    finally:
        get_settings.cache_clear()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")
