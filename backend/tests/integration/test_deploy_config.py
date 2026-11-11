"""Deploy wiring: Render and docker-compose must build and boot the app (review C1, C2)."""

from __future__ import annotations

from pathlib import Path

from app.db import normalise_database_url

ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_render_and_compose_build_backend_from_repo_root() -> None:
    assert "dockerContext: ." in _read("render.yaml").replace("dockerContext: ./backend", "")
    compose = _read("docker-compose.yml")
    assert "context: .\n" in compose
    assert "dockerfile: backend/Dockerfile" in compose


def test_render_uses_only_free_plans() -> None:
    plans = [
        line.split(":", 1)[1].strip()
        for line in _read("render.yaml").splitlines()
        if line.strip().startswith("plan:")
    ]
    assert plans  # the api service and the database both declare a plan
    assert set(plans) == {"free"}


def test_backend_image_starts_the_app_instance() -> None:
    cmd = next(line for line in _read("backend/Dockerfile").splitlines() if line.startswith("CMD"))
    assert "app.main:app" in cmd
    assert "--factory" not in cmd


def test_compose_healthcheck_uses_api_v1_health() -> None:
    healthcheck = next(
        line for line in _read("docker-compose.yml").splitlines() if "urlopen" in line
    )
    assert "/api/v1/health" in healthcheck


def test_web_image_builds_from_repo_root_with_shared_fixtures() -> None:
    # The frontend imports ../../../shared/fixtures, which lies outside frontend/.
    compose = _read("docker-compose.yml")
    assert "dockerfile: frontend/Dockerfile" in compose
    dockerfile = _read("frontend/Dockerfile")
    assert "COPY shared/fixtures" in dockerfile
    ignore = _read(".dockerignore")
    assert "**/node_modules" in ignore
    assert ".git" in ignore


def test_frontend_dockerfile_is_not_empty() -> None:
    dockerfile = _read("frontend/Dockerfile")
    assert dockerfile.strip()
    assert "nginx" in dockerfile
    assert "ARG VITE_API_BASE_URL" in dockerfile  # Vite bakes env in at build time


def test_postgres_urls_use_psycopg() -> None:
    assert normalise_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url("sqlite:///x.db") == "sqlite:///x.db"
    already = "postgresql+psycopg://u:p@h/db"
    assert normalise_database_url(already) == already


def _requirement_names(relative: str) -> set[str]:
    names = set()
    for line in _read(relative).splitlines():
        spec = line.split("#", 1)[0].strip()
        if spec and not spec.startswith("-"):
            names.add(spec.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip())
    return names


def test_runtime_requirements() -> None:
    runtime = _requirement_names("backend/requirements.txt")
    assert {"psycopg", "httpx"} <= runtime
    assert "paypal-agent-toolkit" not in runtime  # only a test imports it
    assert "paypal-agent-toolkit" in _requirement_names("backend/requirements-dev.txt")
