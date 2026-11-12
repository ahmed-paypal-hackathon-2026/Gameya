"""Task 8: the circle engine checks the ledger invariants before any payout."""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import Contribution, Cycle, Membership, Payout  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.domain.circle_engine import InvariantViolation, close_cycle  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.paypal.payouts import PayoutResult  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

ORDER = ["amina", "sara", "youssef", "karim", "omar", "lina"]  # slot k -> member
CYCLE0 = f"{GOLDEN_CIRCLE_ID}-c0"


class FakePayouts:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> PayoutResult:
        self.calls.append(kwargs)
        return PayoutResult(batch_id=f"PB-{len(self.calls)}", status="PENDING")


async def _token() -> str:
    return "t"


@pytest.fixture(name="session")
def _session() -> Iterator[Any]:
    get_settings.cache_clear()
    with TestClient(create_app()):  # sets up the engine and tables
        session = next(get_db())
        try:
            seed_golden_circle(session)
            for index, name in enumerate(ORDER):
                session.get(Cycle, f"{GOLDEN_CIRCLE_ID}-c{index}").payout_member_id = f"mem-{name}"
                session.get(Membership, f"ms-{GOLDEN_CIRCLE_ID}-mem-{name}").slot_index = index
            session.commit()
            yield session
        finally:
            session.rollback()
            session.close()
    get_settings.cache_clear()


def _pay(session: Any, cycle_id: str, names: list[str]) -> None:
    for name in names:
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


async def test_close_pays_the_slot_owner_the_full_pot(session: Any) -> None:
    _pay(session, CYCLE0, ORDER)
    payouts = FakePayouts()
    payout = await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    assert payout.amount == Decimal("600.00")
    assert payout.sender_batch_id.startswith("gameya-circle-golden-01-0-")
    assert payout.paypal_batch_id == "PB-1"
    call = payouts.calls[0]
    assert call["receiver_email"] == "amina-buyer@gameya-sandbox.dev"
    assert call["amount"] == Decimal("600.00")
    assert call["sender_batch_id"] == payout.sender_batch_id
    assert session.get(Cycle, CYCLE0).status == "PAID_OUT"


async def test_close_refuses_when_the_pot_is_short(session: Any) -> None:
    _pay(session, CYCLE0, ORDER[:5])
    payouts = FakePayouts()
    with pytest.raises(InvariantViolation, match="pot"):
        await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    assert payouts.calls == []
    assert session.scalars(select(Payout)).all() == []


async def test_close_twice_pays_once(session: Any) -> None:
    _pay(session, CYCLE0, ORDER)
    payouts = FakePayouts()
    first = await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    second = await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    assert first.id == second.id
    assert len(payouts.calls) == 1


async def test_member_paid_before_cannot_be_paid_again(session: Any) -> None:
    _pay(session, CYCLE0, ORDER)
    await close_cycle(session, CYCLE0, payouts=FakePayouts(), token_provider=_token)
    cycle1 = f"{GOLDEN_CIRCLE_ID}-c1"
    session.get(Cycle, cycle1).payout_member_id = "mem-amina"  # tampered order
    _pay(session, cycle1, ORDER)
    payouts = FakePayouts()
    with pytest.raises(InvariantViolation, match="already"):
        await close_cycle(session, cycle1, payouts=payouts, token_provider=_token)
    assert payouts.calls == []


async def test_no_recipient_means_no_payout(session: Any) -> None:
    session.get(Cycle, CYCLE0).payout_member_id = None  # order not approved yet
    _pay(session, CYCLE0, ORDER)
    payouts = FakePayouts()
    with pytest.raises(InvariantViolation, match="order"):
        await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    assert payouts.calls == []


async def test_payouts_disabled_records_a_blocked_payout(
    session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAYOUTS_ENABLED", "0")
    get_settings.cache_clear()
    _pay(session, CYCLE0, ORDER)
    payouts = FakePayouts()
    payout = await close_cycle(session, CYCLE0, payouts=payouts, token_provider=_token)
    assert payout.status == "BLOCKED"
    assert payouts.calls == []
    assert session.get(Cycle, CYCLE0).status != "PAID_OUT"
    get_settings.cache_clear()


def test_ai_package_cannot_import_payouts() -> None:
    ai_dir = Path(__file__).resolve().parents[2] / "app" / "ai"
    for source in ai_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "paypal.payouts" not in text and "circle_engine" not in text, source.name
