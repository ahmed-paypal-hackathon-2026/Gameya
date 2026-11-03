"""SQLAlchemy declarative base (separate module to avoid import cycles)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
