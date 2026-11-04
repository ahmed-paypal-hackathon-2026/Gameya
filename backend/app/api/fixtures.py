"""Golden-fixture loader for the M2 fixture-backed API (Person 5 / shared assets)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from ..config import FIXTURES_DIR


@cache
def fixture(name: str) -> Any:
    """Parse shared/fixtures/json/<name>.json once; validate what you declare."""
    if not name.endswith(".json"):
        name = f"{name}.json"
    path = Path(FIXTURES_DIR) / name
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    raw = fixture(name)
    return raw if isinstance(raw, str) else json.dumps(raw)
