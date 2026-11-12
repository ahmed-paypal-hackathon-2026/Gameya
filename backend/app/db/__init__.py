"""Engine factory + real session dependency (WP-1.1 takeover).

Engine creation is lazy so tests can call setup_engine() before app startup.
SQLite gets WAL pragmas; PostgreSQL (Render) carries DATABASE_URL unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import models  # noqa: F401  (registers table metadata)
from .base import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _tune_sqlite(conn, _record):  # type: ignore[no-untyped-def]
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def normalise_database_url(url: str) -> str:
    """Point postgres URLs at the psycopg 3 driver (Render hands out `postgres://...`)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def setup_engine(database_url: str) -> Engine:
    global _engine, _SessionLocal
    _engine = create_engine(normalise_database_url(database_url), future=True)
    if _engine.dialect.name == "sqlite":
        event.listen(_engine, "connect", _tune_sqlite)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Create all tables (create_all is idempotent). Engine + Leash namespaces."""
    if _engine is None:
        raise RuntimeError("setup_engine() must be called before init_db()")
    Base.metadata.create_all(_engine)
    ensure_columns(_engine)
    from ..leash.models import LeashBase

    LeashBase.metadata.create_all(_engine)


# Columns added after the first deploy. create_all() never alters an existing table, so an
# existing database (e.g. a Render Postgres from an earlier deploy) gets them here.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("payouts", "recipient_member_id", "VARCHAR(64)"),
    ("invoices", "reminders_sent", "INTEGER DEFAULT 0"),
)


def ensure_columns(engine: Engine) -> None:
    """Add any missing ADDED_COLUMNS to existing tables (idempotent, additive only)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def get_db() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("DB engine not configured")
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def database_is_healthy() -> bool:
    try:
        if _engine is None:
            return False
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health probe never raises
        return False
