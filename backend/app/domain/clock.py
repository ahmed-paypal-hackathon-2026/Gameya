"""The engine's clock. Demo fast-forward moves it ahead; it never moves backwards.

The offset is in memory: a restart returns to real time, which is the safe default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(UTC) + _offset


def advance_to(moment: datetime) -> datetime:
    """Move the clock forward to `moment` (no-op if it is already later)."""
    global _offset
    current = now()
    if moment > current:
        _offset += moment - current
    return now()


def reset() -> None:
    global _offset
    _offset = timedelta(0)
