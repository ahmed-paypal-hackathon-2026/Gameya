"""FastAPI application factory (WP-1.1 takeover).

Policy embedded here:
- Startup guards run first: a non-sandbox PayPal host refuses to boot
  (ENV=local_test is the only override), and production needs SESSION_SECRET.
- Identity is a signed demo-login token (app.auth); client-sent identity or
  organiser headers are never trusted.
- Money truth: PayPal routes are mounted but the webhook receiver refuses
  events until WP-1.2 wires signature verification + idempotency.
- The Leash router mounts with real DB + actor dependency overrides.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .api import admin, circles, cycles, demo, webhooks
from .api.leash import (
    get_actor as _leash_get_actor,
)
from .api.leash import (
    get_db as _leash_get_db,
)
from .api.leash import (
    register_leash_errors,
)
from .api.leash import (
    router as leash_router,
)
from .auth import actor_from_request
from .config import VERSION, get_settings, guard_startup
from .db import database_is_healthy, get_db, init_db, setup_engine
from .schemas.contracts import Environment, HealthResponse, SanityStatus


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    guard_startup(get_settings())
    setup_engine(get_settings().database_url)
    init_db()
    if get_settings().demo_mode:
        _ensure_golden_circle()
    yield


def _ensure_golden_circle() -> None:
    """Demo mode: a fresh database (e.g. a new Render Postgres) gets the golden circle,
    so the organiser screens have members to show. An existing circle is left alone."""
    from .db.models import Circle
    from .db.seed import GOLDEN_CIRCLE_ID, seed_golden_circle

    session = next(get_db())
    try:
        if session.get(Circle, GOLDEN_CIRCLE_ID) is None:
            seed_golden_circle(session)
            session.commit()
    finally:
        session.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Gam'eya API", version=VERSION, lifespan=lifespan)
    settings = get_settings()
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # never "*": see config.DEV_CORS_ORIGINS
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Admin-Token"],
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
    app.dependency_overrides[_leash_get_actor] = actor_from_request
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
