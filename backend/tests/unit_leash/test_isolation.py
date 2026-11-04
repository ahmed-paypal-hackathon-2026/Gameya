"""Leash must stay free of PayPal and LLM imports (TST-25)."""

import ast
from pathlib import Path

import app.leash as leash_pkg

APP = Path(leash_pkg.__file__).resolve().parent.parent
LEASH_DIR = APP / "leash"
API_FILE = APP / "api" / "leash.py"

FORBIDDEN_PREFIXES = (
    "app.services.paypal",
    "app.ai",
    "openai",
    "google.genai",
    "paypal",
)


def _matches(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES)


def imported_modules(source: str, package: str) -> list[str]:
    """Absolute module names imported by ``source``, relative imports resolved
    against ``package``."""
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


def test_resolver_catches_relative_imports() -> None:
    assert forbidden_imports("from ..ai import llm", "app.leash") == [
        "app.ai",
        "app.ai.llm",
    ]
    assert forbidden_imports("from ..services.paypal import x", "app.leash")
    assert forbidden_imports("import openai", "app.leash") == ["openai"]
    assert forbidden_imports("from google.genai import types", "app.leash")
    assert not forbidden_imports("from .facts import BaseFacts", "app.leash")


def test_leash_imports_no_paypal_or_llm() -> None:
    files = sorted(LEASH_DIR.rglob("*.py"))
    assert files
    targets = [(f, "app.leash") for f in files]
    if API_FILE.exists():
        targets.append((API_FILE, "app.api"))
    bad = {
        str(f): hits
        for f, pkg in targets
        if (hits := forbidden_imports(f.read_text(encoding="utf-8"), pkg))
    }
    assert not bad, bad
