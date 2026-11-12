"""Final-review fixes (Task 13): each test reproduces one reviewer finding."""

from __future__ import annotations

import asyncio
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

from app.ai.paypal_rest import PayPalToolError  # noqa: E402
from app.api.cycles import get_now  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import AgentDecision, Contribution, Cycle, Invoice, Membership  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.domain import locks  # noqa: E402
from app.domain.circle_engine import InvariantViolation, close_cycle  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.paypal.executor import get_tool_executor  # noqa: E402
from app.services.paypal.invoicing import get_invoice_gateway  # noqa: E402
from app.services.paypal.payouts import PayoutError, PayoutResult  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ..unit_ai.fakes import FakeExecutor  # noqa: E402
from .test_collection_routes import FakeInvoicing  # noqa: E402

ORDER = ["amina", "sara", "youssef", "karim", "omar", "lina"]
CIRCLE = f"/api/v1/circles/{GOLDEN_CIRCLE_ID}"
CYCLE0 = f"{GOLDEN_CIRCLE_ID}-c0"


async def _token() -> str:
    return "t"


def _apply_order(session: Any) -> None:
    for index, name in enumerate(ORDER):
        session.get(Cycle, f"{GOLDEN_CIRCLE_ID}-c{index}").payout_member_id = f"mem-{name}"
        session.get(Membership, f"ms-{GOLDEN_CIRCLE_ID}-mem-{name}").slot_index = index


def _pay_all(session: Any, cycle_id: str) -> None:
    for name in ORDER:
        session.add(
            Contribution(
                id=f"ct-{cycle_id}-{name}",
                cycle_id=cycle_id,
                member_id=f"mem-{name}",
                kind="INVOICE",
                status="PAID",
                amount=Decimal("100.00"),
                paypal_ref=f"INV-{cycle_id}-{name}",
            )
        )
    session.flush()


@pytest.fixture(name="session")
def _session() -> Iterator[Any]:
    get_settings.cache_clear()
    with TestClient(create_app()):
        session = next(get_db())
        try:
            seed_golden_circle(session)
            _apply_order(session)
            session.commit()
            yield session
        finally:
            session.rollback()
            session.close()
    get_settings.cache_clear()


# ---------- C1: a failed payout is never reported as sent ----------
async def test_a_failed_payout_is_retried_not_reported_as_sent(session: Any) -> None:
    _pay_all(session, CYCLE0)
    attempts: list[str] = []

    async def flaky(**kwargs: Any) -> PayoutResult:
        attempts.append(kwargs["sender_batch_id"])
        if len(attempts) == 1:
            raise PayoutError(403, "dbg")
        return PayoutResult(batch_id="PB-OK", status="PENDING")

    with pytest.raises(PayoutError):
        await close_cycle(session, CYCLE0, payouts=flaky, token_provider=_token)
    failed = session.scalars(select(Cycle).where(Cycle.id == CYCLE0)).one()
    assert failed.status != "PAID_OUT"
    payout = await close_cycle(session, CYCLE0, payouts=flaky, token_provider=_token)
    assert len(attempts) == 2 and attempts[0] == attempts[1]  # same sender_batch_id
    assert payout.paypal_batch_id == "PB-OK"


async def test_a_failed_payout_is_not_counted_in_the_ledger(session: Any) -> None:
    from app.services.ledger_view import ledger

    _pay_all(session, CYCLE0)

    async def failing(**_: Any) -> PayoutResult:
        raise PayoutError(500, None)

    with pytest.raises(PayoutError):
        await close_cycle(session, CYCLE0, payouts=failing, token_provider=_token)
    view = ledger(session, GOLDEN_CIRCLE_ID)
    assert view.paid_out_total == Decimal("0.00")
    assert view.payouts[0].status.value == "failed"


# ---------- C2: PayPal ids change after a demo reset ----------
async def test_sender_batch_id_changes_after_a_reset(session: Any) -> None:
    _pay_all(session, CYCLE0)
    seen: list[str] = []

    async def record(**kwargs: Any) -> PayoutResult:
        seen.append(kwargs["sender_batch_id"])
        return PayoutResult(batch_id=f"PB-{len(seen)}", status="PENDING")

    await close_cycle(session, CYCLE0, payouts=record, token_provider=_token)
    session.commit()
    await asyncio.sleep(0.01)  # a reset gets a new circle created_at
    seed_golden_circle(session)
    _apply_order(session)
    _pay_all(session, CYCLE0)
    await close_cycle(session, CYCLE0, payouts=record, token_provider=_token)
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(s.startswith(f"gameya-{GOLDEN_CIRCLE_ID}-0-") for s in seen)


# ---------- C3: an order change cannot re-pay someone ----------
async def test_a_member_paid_before_is_refused_even_if_the_order_was_rewritten(
    session: Any,
) -> None:
    _pay_all(session, CYCLE0)
    await close_cycle(
        session,
        CYCLE0,
        payouts=lambda **_: _ok(),  # type: ignore[arg-type,return-value]
        token_provider=_token,
    )
    # A rewritten order moves Amina (already paid in cycle 0) to cycle 1.
    session.get(Cycle, CYCLE0).payout_member_id = "mem-lina"
    cycle1 = f"{GOLDEN_CIRCLE_ID}-c1"
    session.get(Cycle, cycle1).payout_member_id = "mem-amina"
    _pay_all(session, cycle1)
    with pytest.raises(InvariantViolation, match="already"):
        await close_cycle(session, cycle1, payouts=_never, token_provider=_token)


async def _ok() -> PayoutResult:
    return PayoutResult(batch_id="PB-1", status="PENDING")


async def _never(**_: Any) -> PayoutResult:
    raise AssertionError("must not pay")


# ---------- I1: one money action per cycle at a time ----------
async def test_cycle_lock_refuses_a_second_concurrent_action() -> None:
    async with locks.cycle_lock("c-x"):
        with pytest.raises(locks.CycleBusy):
            async with locks.cycle_lock("c-x"):
                pass
    async with locks.cycle_lock("c-x"):  # released afterwards
        pass


# ---------- route-level fixes ----------
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
    app.dependency_overrides[get_now] = lambda: datetime(2026, 11, 3, 12, tzinfo=UTC)
    with TestClient(app) as c:
        session = next(get_db())
        try:
            seed_golden_circle(session)
            session.commit()
        finally:
            session.close()
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, name: str = "amina") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": f"persona-{name}"})
    return {"Authorization": f"Bearer {token.json()['token']}"}


def _order_applied() -> None:
    session = next(get_db())
    try:
        _apply_order(session)
        session.commit()
    finally:
        session.close()


def _rows(model: type, **where: Any) -> list[Any]:
    session = next(get_db())
    try:
        query = select(model)
        for key, value in where.items():
            query = query.where(getattr(model, key) == value)
        return list(session.scalars(query).all())
    finally:
        session.close()


# I2: no invoices before the order is approved.
def test_collect_is_refused_until_the_order_is_approved(
    client: TestClient, gateway: FakeInvoicing
) -> None:
    resp = client.post(f"/api/v1/cycles/{CYCLE0}/collect", headers=_bearer(client))
    assert resp.status_code == 409
    assert "order" in resp.json()["detail"]
    assert gateway.created == []


# C2 (invoices): collect request keys change after a reset.
def test_collect_keys_change_after_a_reset(client: TestClient, gateway: FakeInvoicing) -> None:
    _order_applied()
    client.post(f"/api/v1/cycles/{CYCLE0}/collect", headers=_bearer(client))
    first = {key for key, _ in gateway.created}
    session = next(get_db())
    try:
        seed_golden_circle(session)
        session.commit()
    finally:
        session.close()
    _order_applied()
    client.post(f"/api/v1/cycles/{CYCLE0}/collect", headers=_bearer(client))
    second = {key for key, _ in gateway.created} - first
    assert len(first) == 6 and len(second) == 6


# I3: an invoice that PayPal created is stored even when sending it failed.
def test_chase_keeps_an_invoice_created_before_a_send_failure(client: TestClient) -> None:
    _order_applied()
    failing = FakeExecutor(raises={"send_invoice": PayPalToolError("send_invoice", 500, "d")})
    client.app.dependency_overrides[get_tool_executor] = lambda: failing  # type: ignore[attr-defined]
    resp = client.post(f"/api/v1/cycles/{CYCLE0}/chase", headers=_bearer(client))
    assert resp.status_code == 200, resp.text
    created = [c for c in failing.calls if c[0] == "create_invoice"]
    assert len(created) == 6
    stored = _rows(Invoice, cycle_id=CYCLE0)
    assert len(stored) == 6 and all(i.paypal_invoice_id for i in stored)
    # The next chase does not create a second invoice for anyone.
    again = FakeExecutor()
    client.app.dependency_overrides[get_tool_executor] = lambda: again  # type: ignore[attr-defined]
    client.post(f"/api/v1/cycles/{CYCLE0}/chase", headers=_bearer(client))
    assert [c for c in again.calls if c[0] == "create_invoice"] == []


# I4: proposing an unchanged order again is a no-op (no new audit row, approvals kept).
def test_reproposing_an_unchanged_order_keeps_approvals(client: TestClient) -> None:
    amina = _bearer(client)
    client.post(f"{CIRCLE}/order/propose", json={}, headers=amina)
    client.post(f"{CIRCLE}/order/approve", json={"member_id": "mem-amina"}, headers=amina)
    client.post(f"{CIRCLE}/order/propose", json={}, headers=_bearer(client, "omar"))
    decisions = [
        d for d in _rows(AgentDecision, circle_id=GOLDEN_CIRCLE_ID) if d.agent == "fair_order"
    ]
    assert len(decisions) == 1
    for name in ORDER[1:]:
        client.post(
            f"{CIRCLE}/order/approve",
            json={"member_id": f"mem-{name}"},
            headers=_bearer(client, name),
        )
    detail = client.get(CIRCLE).json()
    assert sorted(m["slot_index"] for m in detail["members"]) == [0, 1, 2, 3, 4, 5]


# C3: once money has moved, the order is frozen.
def test_the_order_is_frozen_once_collection_started(
    client: TestClient, gateway: FakeInvoicing
) -> None:
    _order_applied()
    client.post(f"/api/v1/cycles/{CYCLE0}/collect", headers=_bearer(client))
    before = {c.id: c.payout_member_id for c in _rows(Cycle, circle_id=GOLDEN_CIRCLE_ID)}
    text = "I need money for a wedding in November, urgent"
    client.post(
        f"{CIRCLE}/needs",
        json={"member_id": "mem-lina", "raw_text": text, "locale": "en"},
        headers=_bearer(client, "lina"),
    )
    resp = client.post(f"{CIRCLE}/order/propose", json={}, headers=_bearer(client))
    assert resp.status_code == 409
    for name in ORDER:
        client.post(
            f"{CIRCLE}/order/approve",
            json={"member_id": f"mem-{name}"},
            headers=_bearer(client, name),
        )
    after = {c.id: c.payout_member_id for c in _rows(Cycle, circle_id=GOLDEN_CIRCLE_ID)}
    assert after == before


# Minor: an invoice the organiser only marked as paid releases no money.
def test_marked_as_paid_is_not_money_received(client: TestClient, gateway: FakeInvoicing) -> None:
    _order_applied()
    client.post(f"/api/v1/cycles/{CYCLE0}/collect", headers=_bearer(client))
    for invoice_id in list(gateway.status):
        gateway.status[invoice_id] = "MARKED_AS_PAID"
    body = client.post(f"/api/v1/cycles/{CYCLE0}/sync", headers=_bearer(client)).json()
    assert body["newly_paid"] == 0
