"""One money action per cycle at a time (final review I1).

The API runs as a single uvicorn worker, so an in-process lock is enough to stop two
Close (or Chase) presses from racing on the same cycle. A second request does not wait:
it gets CycleBusy, which the routes turn into 409, so the event loop never blocks on a
database row lock held by the first request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_busy: set[str] = set()


class CycleBusy(RuntimeError):
    pass


@asynccontextmanager
async def cycle_lock(cycle_id: str) -> AsyncIterator[None]:
    if cycle_id in _busy:
        raise CycleBusy(f"another action is already running on cycle {cycle_id}")
    _busy.add(cycle_id)
    try:
        yield
    finally:
        _busy.discard(cycle_id)
