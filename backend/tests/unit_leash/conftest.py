from collections.abc import Iterator
from typing import Any

import pytest
from app.leash.models import LeashBase
from sqlalchemy import Connection, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLAlchemy's documented pysqlite SAVEPOINT workaround: stop the driver
    # from managing transactions so begin_nested() rolls back correctly.
    @event.listens_for(engine, "connect")
    def _do_connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _do_begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN")

    LeashBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
