"""Task 7: collect contributions with real invoices; Smart Chase for the late member."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.ai.toolkit_adapter import CreateInvoiceParameters  # noqa: E402
from app.api.cycles import get_now  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import Contribution, Cycle, Invoice  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.paypal.executor import get_tool_executor  # noqa: E402
from app.services.paypal.invoicing import get_invoice_gateway  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ..unit_ai.fakes import FakeExecutor  # noqa: E402

CYCLE = f"{GOLDEN_CIRCLE_ID}-c0"
CYCLE_URL = f"/api/v1/cycles/{CYCLE}"
MEMBERS = ["amina", "omar", "sara", "youssef", "lina", "karim"]
ORDER = ["amina", "sara", "youssef", "karim", "omar", "lina"]


class FakeInvoicing:
    """Records invoice calls; statuses are set by the test."""

    def __init__(self) -> None:
        self.created: list[tuple[str, CreateInvoiceParameters]] = []
        self.status: dict[str, str] = {}

    async def create_and_send(self, params: CreateInvoiceParameters, *, key: str) -> str:
        self.created.append((key, params))
        invoice_id = f"INV2-{len(self.created):04d}"
        self.status[invoice_id] = "SENT"
        return invoice_id

    async def get_status(self, invoice_id: str) -> str:
        return self.status[invoice_id]


@pytest.fixture(name="gateway")
def _gateway() -> FakeInvoicing:
    return FakeInvoicing()


@pytest.fixture(name="toolkit")
def _toolkit() -> FakeExecutor:
    return FakeExecutor()


@pytest.fixture(name="client")
def _client(
    monkeypatch: pytest.MonkeyPatch, gateway: FakeInvoicing, toolkit: FakeExecutor
) -> Iterator[TestClient]:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_invoice_gateway] = lambda: gateway
    app.dependency_overrides[get_tool_executor] = lambda: toolkit
    # One day after the grace period of cycle 1 (due 2026-11-01).
    app.dependency_overrides[get_now] = lambda: datetime(2026, 11, 3, 12, tzinfo=UTC)
    with TestClient(app) as c:
        session = next(get_db())
        try:
            seed_golden_circle(session)
            # The payout order is approved: collection is refused before that.
            for index, name in enumerate(ORDER):
                session.get(Cycle, f"{GOLDEN_CIRCLE_ID}-c{index}").payout_member_id = f"mem-{name}"
            session.commit()
        finally:
            session.close()
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, name: str = "amina") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": f"persona-{name}"})
    return {"Authorization": f"Bearer {token.json()['token']}"}


def _rows(model: type, **where: Any) -> list[Any]:
    session = next(get_db())
    try:
        query = select(model)
        for key, value in where.items():
            query = query.where(getattr(model, key) == value)
        return list(session.scalars(query).all())
    finally:
        session.close()


def _pay(gateway: FakeInvoicing, client: TestClient, names: list[str]) -> None:
    by_member = {i.member_id: i.paypal_invoice_id for i in _rows(Invoice, cycle_id=CYCLE)}
    for name in names:
        gateway.status[by_member[f"mem-{name}"]] = "PAID"


def test_collect_creates_one_invoice_per_member_once(
    client: TestClient, gateway: FakeInvoicing
) -> None:
    for _ in range(2):
        resp = client.post(f"{CYCLE_URL}/collect", headers=_bearer(client))
        assert resp.status_code == 200, resp.text
    assert len(gateway.created) == 6
    keys = [key for key, _ in gateway.created]
    assert len(set(keys)) == 6
    invoices = _rows(Invoice, cycle_id=CYCLE)
    assert len(invoices) == 6 and all(i.paypal_invoice_id for i in invoices)
    params = gateway.created[0][1]
    assert params.items[0].unit_amount.value == "100.00"
    assert len(params.invoice_number) <= 25  # PayPal's limit
    assert params.note  # the reminder text travels in detail.note (Invoicing pitfall)
    emails = {p.primary_recipients[0].billing_info.email_address for _, p in gateway.created}
    assert "omar-buyer@gameya-sandbox.dev" in emails
    assert all(c.status == "INVOICED" for c in _rows(Contribution, cycle_id=CYCLE))


def test_sync_marks_paid_invoices_as_paid_contributions(
    client: TestClient, gateway: FakeInvoicing
) -> None:
    client.post(f"{CYCLE_URL}/collect", headers=_bearer(client))
    _pay(gateway, client, ["amina", "sara", "youssef", "lina", "karim"])
    resp = client.post(f"{CYCLE_URL}/sync", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["newly_paid"] == 5 and body["unpaid_member_ids"] == ["mem-omar"]
    paid = [c for c in _rows(Contribution, cycle_id=CYCLE) if c.status == "PAID"]
    assert len(paid) == 5 and all(c.paypal_ref and c.paypal_ref.startswith("INV2-") for c in paid)
    again = client.post(f"{CYCLE_URL}/sync", headers=_bearer(client)).json()
    assert again["newly_paid"] == 0


def test_all_paid_makes_the_cycle_ready(client: TestClient, gateway: FakeInvoicing) -> None:
    client.post(f"{CYCLE_URL}/collect", headers=_bearer(client))
    _pay(gateway, client, MEMBERS)
    body = client.post(f"{CYCLE_URL}/sync", headers=_bearer(client)).json()
    assert body["cycle_status"] == "ready"


def test_chase_reminds_only_the_late_member(
    client: TestClient, gateway: FakeInvoicing, toolkit: FakeExecutor
) -> None:
    client.post(f"{CYCLE_URL}/collect", headers=_bearer(client))
    _pay(gateway, client, ["amina", "sara", "youssef", "lina", "karim"])
    client.post(f"{CYCLE_URL}/sync", headers=_bearer(client))
    resp = client.post(f"{CYCLE_URL}/chase", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    actions = resp.json()["actions"]
    assert [(a["member_id"], a["action"]) for a in actions] == [("mem-omar", "reminder")]
    assert [call[0] for call in toolkit.calls] == ["send_invoice_reminder"]
    omar_invoice = next(i for i in _rows(Invoice, cycle_id=CYCLE) if i.member_id == "mem-omar")
    assert toolkit.calls[0][1].invoice_id == omar_invoice.paypal_invoice_id  # type: ignore[attr-defined]
    assert omar_invoice.reminders_sent == 1


def test_chase_invoices_a_member_without_an_invoice_for_the_contribution_only(
    client: TestClient, toolkit: FakeExecutor
) -> None:
    resp = client.post(f"{CYCLE_URL}/chase", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    created = [c for c in toolkit.calls if c[0] == "create_invoice"]
    assert len(created) == 6
    for call in created:
        params = call[1]
        assert Decimal(params.items[0].unit_amount.value) == Decimal("100.00")  # type: ignore[attr-defined]
        assert len(params.invoice_number) <= 25  # type: ignore[attr-defined]
    assert len(_rows(Invoice, cycle_id=CYCLE)) == 6


def test_chase_before_the_grace_period_sends_nothing(
    client: TestClient, toolkit: FakeExecutor
) -> None:
    client.app.dependency_overrides[get_now] = lambda: datetime(  # type: ignore[attr-defined]
        2026, 11, 1, 6, tzinfo=UTC
    )
    resp = client.post(f"{CYCLE_URL}/chase", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    assert toolkit.calls == []


@pytest.mark.parametrize("action", ["collect", "sync", "chase"])
def test_collection_needs_the_organiser_token(client: TestClient, action: str) -> None:
    assert client.post(f"{CYCLE_URL}/{action}").status_code == 401
    assert client.post(f"{CYCLE_URL}/{action}", headers=_bearer(client, "omar")).status_code in (
        401,
        403,
    )


def test_unknown_cycle_is_404(client: TestClient) -> None:
    resp = client.post("/api/v1/cycles/nope/collect", headers=_bearer(client))
    assert resp.status_code == 404


def test_ai_package_cannot_reach_engine_paypal_modules() -> None:
    ai_dir = Path(__file__).resolve().parents[2] / "app" / "ai"
    banned = (
        "services.paypal.invoicing",
        "services.paypal.payouts",
        "domain.circle_engine",
        "domain.collection",
    )
    for source in ai_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for name in banned:
            assert name not in text, f"{source.name} imports {name}"


def test_a_member_sees_only_their_own_invoice_with_a_paypal_pay_link(
    client: TestClient, gateway: FakeInvoicing
) -> None:
    client.post(f"{CYCLE_URL}/collect", headers=_bearer(client))
    resp = client.get(
        f"/api/v1/circles/{GOLDEN_CIRCLE_ID}/my-invoices", headers=_bearer(client, "omar")
    )
    assert resp.status_code == 200, resp.text
    invoices = resp.json()
    assert len(invoices) == 1
    mine = invoices[0]
    assert mine["cycle_index"] == 0 and mine["amount"] == "100.00"
    assert mine["pay_url"] == (
        f"https://www.sandbox.paypal.com/invoice/p/#{mine['paypal_invoice_id']}"
    )
    assert mine["paid"] is False
    assert client.get(f"/api/v1/circles/{GOLDEN_CIRCLE_ID}/my-invoices").status_code == 401
