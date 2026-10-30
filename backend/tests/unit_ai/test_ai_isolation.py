"""The AI lane must never reach Payouts, Leash or the database (plan §13.2)."""

import ast
from pathlib import Path

import app.ai as ai_pkg

AI_DIR = Path(ai_pkg.__file__).resolve().parent
APP = AI_DIR.parent

FORBIDDEN_PREFIXES = (
    "app.services.paypal",
    "app.leash",
    "app.db",
    "sqlalchemy",
)


def _matches(module: str) -> bool:
    if "payout" in module.lower():
        return True
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES)


def imported_modules(source: str, package: str) -> list[str]:
    """Absolute module names imported by ``source``; relative imports are
    resolved against ``package``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*parts, base] if base else parts)
            found.append(base)
            found += [f"{base}.{a.name}" for a in node.names]
    return found


def forbidden_imports(source: str, package: str) -> list[str]:
    return [m for m in imported_modules(source, package) if _matches(m)]


def _package_of(path: Path) -> str:
    rel = path.parent.relative_to(APP.parent)
    return ".".join(rel.parts)


def test_resolver_catches_forbidden_imports() -> None:
    assert forbidden_imports("from ..services.paypal import payouts", "app.ai")
    assert forbidden_imports("from ..leash import decide", "app.ai")
    assert forbidden_imports("from ..db.session import get_session", "app.ai")
    assert forbidden_imports("import sqlalchemy", "app.ai")
    assert forbidden_imports("from sqlalchemy.orm import Session", "app.ai")
    assert forbidden_imports("from ...services.paypal import x", "app.ai.prompts")
    assert forbidden_imports("from .payout_helper import x", "app.ai")


def test_resolver_allows_legitimate_imports() -> None:
    assert not forbidden_imports("from ..ml.stress_test import reserve_setup_fee", "app.ai")
    assert not forbidden_imports("from ..schemas.contracts import AgentName", "app.ai")
    assert not forbidden_imports("from . import _llm_port", "app.ai")


def test_ai_imports_nothing_forbidden() -> None:
    files = sorted(AI_DIR.rglob("*.py"))
    assert files
    bad = {
        str(f.relative_to(APP)): hits
        for f in files
        if (hits := forbidden_imports(f.read_text(encoding="utf-8"), _package_of(f)))
    }
    assert not bad, bad
