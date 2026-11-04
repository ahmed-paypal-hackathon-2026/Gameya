"""Application settings and startup guards (WP-1.1 takeover).

Reads the environment directly (no extra dependency). Secrets only ever live in
.env; this module is the single place that touches them.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", str(REPO_ROOT / "shared" / "fixtures" / "json")))

VERSION = "0.1.0-mvp"
PRODUCTION = "production"
# Local dev servers (vite dev and preview); production must list its origins explicitly.
DEV_CORS_ORIGINS = ["http://localhost:5173", "http://localhost:4173"]


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.env: str = os.environ.get("ENV", "demo")
        # Admin/demo routes (fast-forward, reset) exist only when explicitly enabled.
        self.demo_mode: bool = _flag(os.environ.get("DEMO_MODE", "0"))
        self.database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./gameya.db")
        self.paypal_api_base_url: str = os.environ.get("PAYPAL_API_BASE_URL", "")
        self.paypal_client_id: str = os.environ.get("PAYPAL_CLIENT_ID", "")
        self.paypal_client_secret: str = os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self.paypal_webhook_id: str = os.environ.get("PAYPAL_WEBHOOK_ID", "")
        self.gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
        # Signs demo-login tokens. Production must set it (guard_startup); elsewhere a
        # per-process random secret is fine because tokens only need to outlive one run.
        self.session_secret_configured: bool = bool(os.environ.get("SESSION_SECRET"))
        self.session_secret: str = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
        # Required in X-Admin-Token for admin routes in production; empty disables them there.
        self.admin_token: str = os.environ.get("ADMIN_TOKEN", "")
        configured = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
        self.cors_origins: list[str] = configured or (
            [] if self.env == PRODUCTION else list(DEV_CORS_ORIGINS)
        )

    @property
    def is_production(self) -> bool:
        return self.env == PRODUCTION

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


def guard_startup(settings: Settings) -> None:
    """Every check that must pass before the app serves a request."""
    guard_paypal_base_url(settings)
    if settings.is_production and not settings.session_secret_configured:
        raise RuntimeError("SESSION_SECRET must be set in production (it signs login tokens)")


def guard_paypal_base_url(settings: Settings) -> None:
    """Refuse to boot unless the PayPal base URL is an HTTPS sandbox host.

    The host is parsed, not substring-matched: "https://evil.example/?sandbox.paypal.com"
    or "sandbox.paypal.com.evil.example" must not pass. ENV=local_test is the only
    escape hatch; an empty base URL is the fixture-backed demo until WP-1.2.
    """
    if not settings.paypal_api_base_url or settings.env == "local_test":
        return
    parts = urlsplit(settings.paypal_api_base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (
        host == "sandbox.paypal.com" or host.endswith(".sandbox.paypal.com")
    ):
        raise RuntimeError(
            "PAYPAL_API_BASE_URL must be an https://*.sandbox.paypal.com URL "
            f"(set ENV=local_test to override); got {settings.paypal_api_base_url!r}"
        )
