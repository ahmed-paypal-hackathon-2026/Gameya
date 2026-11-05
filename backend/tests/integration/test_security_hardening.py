"""Security hardening of the WP-1.1 skeleton (review of PR #10).

Identity comes from a server-signed demo-login token, never from headers the
client chooses; organiser rights come from server-side circle data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

_DB_DIR = Path(__file__).resolve().parents[1] / ".pytest-db"
_DB_DIR.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_DIR / 'gameya.db'}")

from app.config import Settings, get_settings, guard_startup  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

GOLDEN = "circle-golden-01"


@pytest.fixture(name="make_client")
def _make_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Build an app under a given environment; settings are re-read each time."""
    clients: list[TestClient] = []

    def make(**env: str) -> TestClient:
        for key in ("ENV", "DEMO_MODE", "ADMIN_TOKEN", "SESSION_SECRET", "CORS_ORIGINS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
    get_settings.cache_clear()


def _login(client: TestClient, persona: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/demo-login", json={"persona_id": persona})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ---------- identity: no more trusting client-chosen headers ----------
def test_client_chosen_actor_headers_are_not_identity(make_client) -> None:
    client = make_client()
    forged = {"X-Actor-Member": "mem-omar", "X-Actor-Organiser-Of": GOLDEN}
    assert client.get(f"/api/v1/circles/{GOLDEN}/leash/inbox", headers=forged).status_code == 401


def test_signed_login_token_lets_the_organiser_in(make_client) -> None:
    client = make_client()
    headers = _login(client, "persona-amina")
    assert client.get(f"/api/v1/circles/{GOLDEN}/leash/inbox", headers=headers).status_code == 200


def test_unsigned_or_tampered_tokens_are_rejected(make_client) -> None:
    client = make_client()
    good = _login(client, "persona-omar")["Authorization"].removeprefix("Bearer ")
    tampered = "persona-amina" + good[good.index(".") :]
    for token in ("persona-amina", tampered, "", "x.y.z"):
        resp = client.get(
            f"/api/v1/circles/{GOLDEN}/leash/policy", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401, token


# Leash's contract maps ORGANISER_ONLY to HTTP 401 (app/leash/service.py); asserting
# the error code proves the denial came from the organiser check, not from login.
def test_a_member_cannot_act_as_organiser(make_client) -> None:
    client = make_client()
    headers = _login(client, "persona-omar")
    resp = client.get(f"/api/v1/circles/{GOLDEN}/leash/inbox", headers=headers)
    assert resp.json()["error"]["code"] == "ORGANISER_ONLY"


def test_an_organiser_cannot_claim_another_circle(make_client) -> None:
    client = make_client()
    headers = _login(client, "persona-amina")
    resp = client.get("/api/v1/circles/circle-someone-else/leash/inbox", headers=headers)
    assert resp.json()["error"]["code"] == "ORGANISER_ONLY"


# ---------- demo/admin routes ----------
def test_demo_mode_is_off_unless_asked_for() -> None:
    os.environ.pop("DEMO_MODE", None)
    assert Settings().demo_mode is False


def test_admin_routes_do_not_exist_without_demo_mode(make_client) -> None:
    client = make_client()
    assert client.post("/api/v1/admin/reset-demo").status_code == 404


def test_production_admin_routes_need_the_admin_token(make_client) -> None:
    client = make_client(ENV="production", DEMO_MODE="1", ADMIN_TOKEN="s3cret-admin",
                         SESSION_SECRET="test-session-secret")  # fmt: skip
    assert client.post("/api/v1/admin/reset-demo").status_code == 403
    wrong = {"X-Admin-Token": "guess"}
    assert client.post("/api/v1/admin/reset-demo", headers=wrong).status_code == 403
    right = {"X-Admin-Token": "s3cret-admin"}
    assert client.post("/api/v1/admin/reset-demo", headers=right).status_code == 200


# ---------- CORS ----------
def test_cors_only_allows_configured_origins(make_client) -> None:
    client = make_client(CORS_ORIGINS="https://gameya-web.onrender.com")
    preflight = {"Access-Control-Request-Method": "POST"}
    evil = client.options("/api/v1/health", headers={**preflight, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    ok = client.options(
        "/api/v1/health", headers={**preflight, "Origin": "https://gameya-web.onrender.com"}
    )
    assert ok.headers.get("access-control-allow-origin") == "https://gameya-web.onrender.com"


def test_cors_is_never_a_wildcard_by_default(make_client) -> None:
    client = make_client()
    resp = client.options(
        "/api/v1/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") not in ("*", "https://evil.example")


# ---------- startup guards ----------
def _settings(**env: str) -> Settings:
    for key in ("ENV", "PAYPAL_API_BASE_URL", "SESSION_SECRET"):
        os.environ.pop(key, None)
    os.environ.update(env)
    try:
        return Settings()
    finally:
        for key in env:
            os.environ.pop(key, None)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/?sandbox.paypal.com",
        "https://sandbox.paypal.com.evil.example",
        "http://api-m.sandbox.paypal.com",
        "https://api-m.paypal.com",
    ],
)
def test_sandbox_guard_checks_the_real_host(url: str) -> None:
    with pytest.raises(RuntimeError, match="sandbox"):
        guard_startup(_settings(ENV="demo", PAYPAL_API_BASE_URL=url))


def test_sandbox_guard_accepts_the_sandbox_api() -> None:
    guard_startup(_settings(ENV="demo", PAYPAL_API_BASE_URL="https://api-m.sandbox.paypal.com"))


def test_production_refuses_to_boot_without_a_session_secret() -> None:
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        guard_startup(_settings(ENV="production"))


# ---------- deploy wiring ----------
def test_render_health_check_points_at_the_real_health_route() -> None:
    render = (Path(__file__).resolve().parents[3] / "render.yaml").read_text(encoding="utf-8")
    assert "healthCheckPath: /api/v1/health" in render
