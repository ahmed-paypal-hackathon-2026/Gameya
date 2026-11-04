"""Prefixed ULID identifiers for Leash rows."""

from typing import Literal

from ulid import ULID


def new_id(prefix: Literal["lpol", "lsd"]) -> str:
    return f"{prefix}_{ULID()}"
