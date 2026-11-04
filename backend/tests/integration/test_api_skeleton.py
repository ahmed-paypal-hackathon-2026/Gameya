"""WP-1.1 skeleton smoke: the fixture-backed API serves contract-valid shapes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"  # backend/.pytest-db
_DB_DIR.mkdir(exist_ok=True)
for _leftover in ("gameya.db", "gameya.db-wal", "gameya.db-shm"):
    (_DB_DIR / _leftover).unlink(missing_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.main import create_app  # noqa: E402  (imports must follow env seeding)
from fastapi.testclient import TestClient  # noqa: E402

from ..fixtures import fixture_json  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "json"


@pytest.fixture(name="client")
def _client():
    with TestClient(create_app()) as c:
        yield c


def _fixture(name: str) -> Path:  # path for exact-parity assertions
    return fixture_json(name)


def test_health_matches_contract(client: TestClient) -> None:
    got = client.get("/api/v1/health").json()
    assert got["status"] == "healthy"
    assert got["database"] == "healthy"
    assert got["version"].startswith("0.1.0")


@pytest.mark.parametrize(
    ("method", "path", "body", "fixture_name"),
    [
        (
            "post",
            "/api/v1/circles",
            {
                "spec": {
                    "name": "El Shorbagh",
                    "member_count": 6,
                    "contribution_amount": "100.00",
                    "start_date": "2026-11-01",
                    "confirmed": True,
                }
            },
            "circles-create",
        ),
        ("get", "/api/v1/circles/c-golden", None, "circle-get"),
        ("post", "/api/v1/circles/c-golden/join", {"member_id": "persona-karim"}, "circle-join"),
        (
            "post",
            "/api/v1/circles/c-golden/needs",
            {"member_id": "persona-karim", "raw_text": "laptop in december", "locale": "en"},
            "circle-needs",
        ),
        ("post", "/api/v1/circles/c-golden/order/propose", {"seed": 42}, "circle-order-propose"),
        (
            "post",
            "/api/v1/circles/c-golden/order/approve",
            {"member_id": "persona-sara"},
            "circle-order-propose",
        ),
        (
            "post",
            "/api/v1/circles/c-golden/copilot",
            {"member_id": "persona-youssef", "question": "When is my payout?"},
            "circle-copilot",
        ),
        (
            "post",
            "/api/v1/circles/c-golden/swaps",
            {
                "request": {
                    "request_id": "swap-1",
                    "requester_id": "persona-karim",
                    "requester_slot_index": 5,
                    "target_slot_index": 4,
                    "reason": "March is my busiest month.",
                }
            },
            "circle-swaps",
        ),
        (
            "post",
            "/api/v1/circles/c-golden/swaps/swap-1/approve",
            {"approver_id": "persona-youssef"},
            "circle-swaps",
        ),
        (
            "post",
            "/api/v1/circles/c-golden/stress-test",
            {"reserve_pcts": ["0", "3", "10"]},
            "circles-stress-test",
        ),
        ("get", "/api/v1/circles/c-golden/ledger", None, "circle-ledger"),
        ("get", "/api/v1/circles/c-golden/audit", None, "circle-audit"),
        ("get", "/api/v1/circles/c-golden/reliability", None, "circle-reliability"),
        (
            "post",
            "/api/v1/agent/circle/chat",
            {"history": [{"role": "user", "content": "Make a circle of 6 friends, $100 a month."}]},
            "agent-circle-chat",
        ),
        (
            "post",
            "/api/v1/cycles/cyc-1/pay-now",
            {"member_id": "persona-omar"},
            "cycles-pay-now",
        ),
        ("post", "/api/v1/admin/reset-demo", None, None),
    ],
)
def test_fixture_routes_serve_golden_shapes(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, object] | None,
    fixture_name: str | None,
) -> None:
    if path == "/api/v1/admin/reset-demo":
        resp = client.post(path)
        assert resp.status_code == 200
        assert resp.json() == {"reset": True, "circle_id": "circle-golden-01"}
        return
    if method == "get":
        resp = client.get(path)
    else:
        resp = client.request(method, path, json=body)
    assert resp.status_code == 200, resp.text
    if fixture_name:
        assert resp.json() == json.loads(_fixture(fixture_name).read_text("utf-8"))


def test_mediator_approval_envelope(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/cycles/cyc-1/mediation/OPT-1/approve", json={"approving_member_id": "m2"}
    )
    assert resp.status_code == 200
    assert resp.json()["option_id"] == "OPT-1"


def test_webhook_refuses_until_wp_1_2(client: TestClient) -> None:
    resp = client.post("/api/v1/webhooks/paypal", json={"id": "WH-1"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "WEBHOOK_NOT_CONFIGURED"


def test_demo_login_roundtrip(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/demo-login", json={"persona_id": "persona-amina"})
    assert resp.status_code == 200
    assert resp.json()["persona"]["role"] == "organiser"
    assert client.post("/api/v1/auth/demo-login", json={"persona_id": "nope"}).status_code == 404


def test_leash_actor_requires_header(client: TestClient) -> None:
    assert client.get("/api/v1/circles/c-golden/leash/policy").status_code == 401
    headers = {"X-Actor-Member": "persona-amina", "X-Actor-Organiser-Of": "c-golden"}
    assert client.get("/api/v1/circles/c-golden/leash/policy", headers=headers).status_code == 200


def test_bad_stress_body_is_422(client: TestClient) -> None:
    resp = client.post("/api/v1/circles/c-golden/stress-test", json={"reserve_pcts": ["x"]})
    assert resp.status_code == 422
