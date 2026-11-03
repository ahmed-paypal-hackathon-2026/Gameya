"""FastAPI application factory (WP-1.1 takeover).

Policy embedded here:
- Sandbox guard runs on startup; a non-sandbox base URL refuses to boot
  (ENV=local_test is the only override).
- Money truth: PayPal routes are mounted but the webhook receiver refuses
  events until WP-1.2 wires signature verification + idempotency.
- The Leash router mounts with real DB + actor dependency overrides.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .api import admin, circles, cycles, demo, webhooks
from .api.leash import (
    Actor,
    register_leash_errors,
)
from .api.leash import (
    get_actor as _leash_get_actor,
)
from .api.leash import (
    get_db as _leash_get_db,
)
from .api.leash import (
    router as leash_router,
)
from .config import VERSION, get_settings, guard_paypal_base_url
from .db import database_is_healthy, get_db, init_db, setup_engine
from .schemas.contracts import Environment, HealthResponse, SanityStatus


@asynccontextmanager
async def lifespan(app: FastAPI) -> Iterator[None]:
    guard_paypal_base_url(get_settings())
    setup_engine(get_settings().database_url)
    init_db()
    yield


def _actor_from_headers(request: Request) -> Actor:
    member = request.headers.get("X-Actor-Member")
    if not member:
        raise HTTPException(status_code=401, detail="X-Actor-Member header required")
    organiser_of = frozenset(
        part for part in request.headers.get("X-Actor-Organiser-Of", "").split(",") if part
    )
    return Actor(user_id=member, organiser_of=organiser_of)


def create_app() -> FastAPI:
    app = FastAPI(title="Gam'eya API", version=VERSION, lifespan=lifespan)
    settings = get_settings()
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _db() -> Iterator[Session]:
        yield from get_db()

    app.include_router(demo.router)
    app.include_router(circles.router)
    app.include_router(circles.agent_router)
    app.include_router(cycles.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    app.include_router(leash_router)
    app.dependency_overrides[_leash_get_db] = _db
    app.dependency_overrides[_leash_get_actor] = _actor_from_headers
    register_leash_errors(app)

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        db_ok = database_is_healthy()
        return HealthResponse(
            status=SanityStatus.HEALTHY if db_ok else SanityStatus.DEGRADED,
            environment=Environment.LOCAL_TEST
            if settings.env == "local_test"
            else Environment.SANDBOX,
            paypal=SanityStatus.HEALTHY if settings.paypal_configured else SanityStatus.DEGRADED,
            llm=SanityStatus.HEALTHY if settings.llm_configured else SanityStatus.DEGRADED,
            database=SanityStatus.HEALTHY if db_ok else SanityStatus.DOWN,
            version=VERSION,
        )

    return app


app = create_app()
