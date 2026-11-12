"""Task 5: the Circle Agent turns a chat into a real PayPal product + billing plan."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.ai.paypal_rest import PayPalToolError  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import AgentDecision, Circle, Membership  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.paypal.executor import get_tool_executor  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ..unit_ai.fakes import FakeExecutor  # noqa: E402

SPEC: dict[str, Any] = {
    "name": "Friends Circle",
    "member_count": 6,
    "contribution_amount": "100.00",
    "currency": "USD",
    "cycle_unit": "MONTH",
    "start_date": "2026-12-01",
    "organiser_id": "mem-amina",
    "purpose": None,
    "confirmed": True,
}


@pytest.fixture(name="fake")
def _fake() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch, fake: FakeExecutor) -> Iterator[TestClient]:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # deterministic agent path
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_tool_executor] = lambda: fake
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, persona: str = "persona-amina") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": persona}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _db_rows(model: type, **where: Any) -> list[Any]:
    session = next(get_db())
    try:
        query = select(model)
        for key, value in where.items():
            query = query.where(getattr(model, key) == value)
        return list(session.scalars(query).all())
    finally:
        session.close()


def test_chat_returns_a_card_and_makes_no_paypal_call(
    client: TestClient, fake: FakeExecutor
) -> None:
    resp = client.post(
        "/api/v1/agent/circle/chat",
        json={
            "history": [
                {"role": "user", "content": "6 friends, $100 a month, starting 1 December"}
            ],
            "confirmed": False,
        },
        headers=_bearer(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"]
    spec = body["turn"]["spec"]
    assert spec["member_count"] == 6 and spec["contribution_amount"] == "100.00"
    assert body["turn"]["needs_confirmation"] is True
    assert fake.calls == []
    assert resp.headers.get("X-Demo-Fixture") is None


def test_chat_cannot_confirm_by_itself(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/agent/circle/chat",
        json={"history": [{"role": "user", "content": "yes"}], "confirmed": True},
        headers=_bearer(client),
    )
    assert resp.status_code == 400


def test_confirm_creates_the_product_and_plan(client: TestClient, fake: FakeExecutor) -> None:
    resp = client.post("/api/v1/circles", json={"spec": SPEC}, headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [call[0] for call in fake.calls] == ["create_product", "create_subscription_plan"]
    product_id, plan_id = body["paypal"]["product_id"], body["paypal"]["plan_id"]
    assert product_id and plan_id
    circle_id = body["circle"]["circle_id"]
    assert len(body["circle"]["members"]) == 6
    circle = _db_rows(Circle, id=circle_id)[0]
    assert (circle.paypal_product_id, circle.paypal_plan_id) == (product_id, plan_id)
    assert circle.organiser_member_id == "mem-amina"
    assert len(_db_rows(Membership, circle_id=circle_id)) == 6
    decisions = _db_rows(AgentDecision, circle_id=circle_id)
    assert decisions and decisions[0].agent == "circle_agent"


def test_reserve_becomes_the_plan_setup_fee(client: TestClient, fake: FakeExecutor) -> None:
    resp = client.post(
        "/api/v1/circles", json={"spec": SPEC, "reserve_pct": "3"}, headers=_bearer(client)
    )
    assert resp.status_code == 200, resp.text
    plan = fake.calls[1][1]
    fee = plan.payment_preferences.setup_fee  # type: ignore[attr-defined]
    assert (fee.currency_code, fee.value) == ("USD", "18.00")
    assert plan.billing_cycles[0].total_cycles == 6  # type: ignore[attr-defined]


def test_creating_a_circle_needs_a_login(client: TestClient, fake: FakeExecutor) -> None:
    assert client.post("/api/v1/circles", json={"spec": SPEC}).status_code == 401
    assert fake.calls == []


def test_an_unconfirmed_spec_is_refused(client: TestClient, fake: FakeExecutor) -> None:
    resp = client.post(
        "/api/v1/circles", json={"spec": {**SPEC, "confirmed": False}}, headers=_bearer(client)
    )
    assert resp.status_code == 422
    assert fake.calls == []


def test_paypal_failure_returns_502_and_saves_no_circle(client: TestClient) -> None:
    failing = FakeExecutor(
        raises={
            "create_subscription_plan": PayPalToolError("create_subscription_plan", 500, "dbg-9")
        }
    )
    client.app.dependency_overrides[get_tool_executor] = lambda: failing  # type: ignore[attr-defined]
    before = len(_db_rows(Circle))
    resp = client.post("/api/v1/circles", json={"spec": SPEC}, headers=_bearer(client))
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "PAYPAL_ERROR"
    assert error["details"]["step"] == "create_subscription_plan"
    assert len(_db_rows(Circle)) == before
