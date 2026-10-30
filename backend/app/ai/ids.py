"""Prefixed ULID identifiers for AI-lane records (<= 30 chars: 3 + 1 + 26)."""

from __future__ import annotations

from typing import Literal

from ulid import ULID

IdPrefix = Literal["agd", "chp", "med"]


def new_id(prefix: IdPrefix) -> str:
    """Return ``{prefix}_{ULID}``; e.g. ``agd_01J...`` (30 chars)."""
    return f"{prefix}_{ULID()}"
