"""Test helper: read a golden fixture as raw text for exact-parity assertions."""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "json"


def fixture_json(name: str) -> Path:
    if not name.endswith(".json"):
        name = f"{name}.json"
    return FIXTURES_DIR / name
