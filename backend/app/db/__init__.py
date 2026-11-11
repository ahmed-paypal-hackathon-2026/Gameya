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
    from ..leash.models import LeashBase

    LeashBase.metadata.create_all(_engine)


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
