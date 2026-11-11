"""Organiser-only reads (review I4), Leash policy GET (review minor), real demo seed (I5)."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.config import get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.models import Circle, Cycle, Membership  # noqa: E402
from app.db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle  # noqa: E402
from app.leash.models import LeashPolicyRow  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
READ_ROUTES = ("reliability", "ledger", "audit")


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DEMO_MODE", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _bearer(client: TestClient, persona: str) -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"persona_id": persona}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _count(model: type) -> int:
    session = next(get_db())
    try:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)
    finally:
        session.close()


# ---------- I4: reliability, ledger and audit are organiser-only ----------
@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_routes_need_a_login(client: TestClient, route: str) -> None:
    assert client.get(f"/api/v1/circles/{GOLDEN_CIRCLE_ID}/{route}").status_code == 401


@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_routes_refuse_members(client: TestClient, route: str) -> None:
    resp = client.get(
        f"/api/v1/circles/{GOLDEN_CIRCLE_ID}/{route}", headers=_bearer(client, "persona-omar")
    )
    assert resp.json()["error"]["code"] == "ORGANISER_ONLY"


@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_routes_serve_the_organiser(client: TestClient, route: str) -> None:
    resp = client.get(
        f"/api/v1/circles/{GOLDEN_CIRCLE_ID}/{route}", headers=_bearer(client, "persona-amina")
    )
    assert resp.status_code == 200


# ---------- Leash policy GET: organiser-only, and a refused read writes nothing ----------
def test_leash_policy_get_is_organiser_only_and_writes_nothing(client: TestClient) -> None:
    before = _count(LeashPolicyRow)
    resp = client.get(
        "/api/v1/circles/arbitrary-xyz/leash/policy", headers=_bearer(client, "persona-omar")
    )
    assert resp.json()["error"]["code"] == "ORGANISER_ONLY"
    assert _count(LeashPolicyRow) == before


# ---------- I5: a real, idempotent golden-circle seed ----------
def test_seed_creates_the_golden_circle_twice_without_duplicates(client: TestClient) -> None:
    session = next(get_db())
    try:
        assert seed_golden_circle(session) == GOLDEN_CIRCLE_ID
        session.commit()
        seed_golden_circle(session)
        session.commit()
        circle = session.get(Circle, GOLDEN_CIRCLE_ID)
        assert circle is not None and circle.size_n == 6
        members = session.scalars(
            select(Membership).where(Membership.circle_id == GOLDEN_CIRCLE_ID)
        ).all()
        assert len(members) == 6
        assert {m.member_id for m in members if m.is_organiser} == {"mem-amina"}
        assert all(m.email.endswith("-buyer@gameya-sandbox.dev") for m in members)
        cycles = session.scalars(
            select(Cycle).where(Cycle.circle_id == GOLDEN_CIRCLE_ID).order_by(Cycle.cycle_index)
        ).all()
        assert [c.cycle_index for c in cycles] == [0, 1, 2, 3, 4, 5]
        assert str(cycles[0].due_date) == "2026-11-01" and str(cycles[5].due_date) == "2027-04-01"
    finally:
        session.close()


def test_reset_demo_reseeds_the_database(client: TestClient) -> None:
    resp = client.post("/api/v1/admin/reset-demo")
    assert resp.status_code == 200
    assert resp.json()["circle_id"] == GOLDEN_CIRCLE_ID
    assert _count(Membership) >= 6


def test_seed_script_check_mode_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seed_demo.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "MONTH" in result.stdout
