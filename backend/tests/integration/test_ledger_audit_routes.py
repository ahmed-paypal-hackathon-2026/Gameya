"""Task 9: the ledger and audit grids are read from the database."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import AgentDecision, Contribution, Cycle, Payout  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CIRCLE = f"/api/v1/circles/{GOLDEN_CIRCLE_ID}"
CYCLE0 = f"{GOLDEN_CIRCLE_ID}-c0"


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        session = next(get_db())
        try:
            seed_golden_circle(session)
            names = ["amina", "omar", "sara", "youssef", "lina", "karim"]
            for i, name in enumerate(names):
                session.add(
                    Contribution(
                        id=f"ct-test-{name}",
                        cycle_id=CYCLE0,
                        member_id=f"mem-{name}",
                        kind="INVOICE",
                        status="PAID" if name != "omar" else "INVOICED",
                        amount=Decimal("100.00"),
                        paypal_ref=f"INV2-TEST-{i}" if name != "omar" else None,
                    )
                )
            session.get(Cycle, CYCLE0).payout_member_id = "mem-amina"
            session.add(
                Payout(
                    id="po-test-1",
                    cycle_id=CYCLE0,
                    amount=Decimal("600.00"),
                    sender_batch_id="gameya-circle-golden-01-0",
                    paypal_request_id="gameya-circle-golden-01-0",
                    paypal_batch_id="PB-REAL-1",
                    status="SUCCESS",
                )
            )
            now = datetime.now(UTC)
            for k, agent in enumerate(["fair_order", "smart_chase"]):
                session.add(
                    AgentDecision(
                        id=f"agd-test-{k}",
                        circle_id=GOLDEN_CIRCLE_ID,
                        agent=agent,
                        decision="d",
                        model="template",
                        tools_json='[{"name": "send_invoice_reminder", '
                        '"arguments": {"invoice_id": "INV2-X"}, "result_summary": null}]'
                        if agent == "smart_chase"
                        else "[]",
                        rationale=f"rationale {k}",
                        actor="mem-amina",
                        created_at=now + timedelta(seconds=k),
                    )
                )
            session.commit()
        finally:
            session.close()
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, name: str = "amina") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": f"persona-{name}"})
    return {"Authorization": f"Bearer {token.json()['token']}"}


def test_ledger_shows_real_paypal_ids_and_totals(client: TestClient) -> None:
    resp = client.get(f"{CIRCLE}/ledger", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Demo-Fixture") is None
    body = resp.json()
    assert len(body["rows"]) == 6
    by_member = {r["member_id"]: r for r in body["rows"]}
    assert by_member["mem-sara"]["paypal_reference"].startswith("INV2-TEST-")
    assert by_member["mem-sara"]["status"] == "paid"
    assert by_member["mem-sara"]["source"] == "invoice"
    assert by_member["mem-sara"]["member_name"] == "Sara"
    assert by_member["mem-omar"]["status"] == "invoiced"
    assert body["contributions_total"] == "500.00"
    assert body["paid_out_total"] == "600.00"
    payout = body["payouts"][0]
    assert (payout["batch_id"], payout["recipient_id"], payout["status"]) == (
        "PB-REAL-1",
        "mem-amina",
        "paid_out",
    )


def test_audit_lists_agent_decisions_newest_first(client: TestClient) -> None:
    resp = client.get(f"{CIRCLE}/audit", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Demo-Fixture") is None
    decisions = resp.json()["decisions"]
    assert [d["agent"] for d in decisions] == ["smart_chase", "fair_order"]
    assert decisions[0]["tool_calls"][0]["name"] == "send_invoice_reminder"
    assert decisions[0]["rationale"] == "rationale 1"


@pytest.mark.parametrize("route", ["ledger", "audit"])
def test_ledger_needs_the_organiser_token(client: TestClient, route: str) -> None:
    assert client.get(f"{CIRCLE}/{route}").status_code == 401
    assert client.get(f"{CIRCLE}/{route}", headers=_bearer(client, "sara")).status_code in (
        401,
        403,
    )
