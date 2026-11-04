"""Application settings and the sandbox-only PayPal startup guard (WP-1.1 takeover).

Reads the environment directly (no extra dependency). Secrets only ever live in
.env; this module is the single place that touches them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", str(REPO_ROOT / "shared" / "fixtures" / "json")))

VERSION = "0.1.0-mvp"


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.env: str = os.environ.get("ENV", "demo")
        self.demo_mode: bool = _flag(os.environ.get("DEMO_MODE", "1"))
        self.database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./gameya.db")
        self.paypal_api_base_url: str = os.environ.get("PAYPAL_API_BASE_URL", "")
        self.paypal_client_id: str = os.environ.get("PAYPAL_CLIENT_ID", "")
        self.paypal_client_secret: str = os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self.paypal_webhook_id: str = os.environ.get("PAYPAL_WEBHOOK_ID", "")
        self.gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
        self.cors_origins: list[str] = [
            origin.strip()
            for origin in os.environ.get("CORS_ORIGINS", "").split(",")
            if origin.strip()
        ]

    @property
    def paypal_configured(self) -> bool:
        return bool(
            self.paypal_api_base_url and self.paypal_client_id and self.paypal_client_secret
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def guard_paypal_base_url(settings: Settings) -> None:
    """Refuse to boot unless the PayPal base URL is a sandbox (app refuses to start).

    ENV=local_test is the only escape hatch; empty base URL is the fixture-backed
    demo mode until WP-1.2 wires credentials.
    """
    if not settings.paypal_api_base_url or settings.env == "local_test":
        return
    if "sandbox.paypal.com" not in settings.paypal_api_base_url:
        raise RuntimeError(
            "PAYPAL_API_BASE_URL must contain sandbox.paypal.com "
            f"(set ENV=local_test to override); got {settings.paypal_api_base_url!r}"
        )
