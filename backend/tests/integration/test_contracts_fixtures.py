"""Every Interface 2/3 endpoint has a golden fixture that validates as a contract.

These are Person 5's clean-room checks: the JSON payload must be exactly the
endpoint response and must round-trip through backend/app/schemas/contracts.py.
Any contract change that breaks a fixture breaks CI.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.schemas import contracts as c
from pydantic import TypeAdapter

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "json"

LIST_MODELS: dict[str, Any] = {
    "cycles-mediation.json": list[c.MediationOption],
    "leash_decisions.json": list[c.LeashDecisionRow],
}

REGISTRY: dict[str, Any] = {
    "auth-demo-login.json": c.DemoLoginResponse,
    "agent-circle-chat.json": c.CircleAgentChatResponse,
    "circles-create.json": c.CircleCreateResponse,
    "circle-get.json": c.CircleDetail,
    "circle-join.json": c.JoinCircleResponse,
    "circle-needs.json": c.NeedExtraction,
    "circle-order-propose.json": c.OrderProposal,
    "circle-copilot.json": c.CopilotAnswer,
    "cycles-pay-now.json": c.PayNowResponse,
    "circle-ledger.json": c.LedgerResponse,
    "circle-audit.json": c.AuditResponse,
    "circle-reliability.json": c.ReliabilityResponse,
    "circles-stress-test.json": c.StressTestResult,
    "circle-swaps.json": c.SwapProposal,
    "health.json": c.HealthResponse,
    "leash_policy.json": c.LeashPolicy,
    "leash_inbox.json": c.LeashInbox,
    "leash_approve.json": c.LeashResolveResponse,
}


def test_fixtures_dir_exists() -> None:
    assert FIXTURES_DIR.is_dir(), f"missing {FIXTURES_DIR}"


def test_fixture_inventory_is_complete() -> None:
    present = {p.name for p in FIXTURES_DIR.glob("*.json")}
    expected = set(REGISTRY) | set(LIST_MODELS)
    assert present == expected, (
        f"fixture inventory drift: missing={expected - present} unexpected={present - expected}"
    )


@pytest.mark.parametrize(("filename", "model"), sorted(REGISTRY.items()))
def test_fixture_validates_against_contract(filename: str, model: Any) -> None:
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    parsed = model.model_validate(raw)
    assert parsed is not None


@pytest.mark.parametrize(("filename", "model"), sorted(LIST_MODELS.items()))
def test_list_fixture_validates_against_contract(filename: str, model: Any) -> None:
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    parsed = TypeAdapter(model).validate_python(raw)
    assert isinstance(parsed, list) and parsed


def test_frontend_contract_mirror_mentions_same_status_values() -> None:
    ts_source = (
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "types" / "contracts.ts"
    ).read_text(encoding="utf-8")
    for status in c.CycleStatus:
        assert f'"{status.value}"' in ts_source, f"contracts.ts missing {status.value}"
    for status in c.ContributionStatus:
        assert f'"{status.value}"' in ts_source, f"contracts.ts missing {status.value}"


def test_money_rejects_floats_and_quantises() -> None:
    parse = TypeAdapter(c.Money).validate_python
    assert parse(Decimal("1.005")) == Decimal("1.01")
    assert parse("2") == Decimal("2.00")
    with pytest.raises(ValueError, match="floats"):
        parse(2.0)


def test_positive_money_rejects_zero() -> None:
    parse = TypeAdapter(c.PositiveMoney).validate_python
    assert parse("0.01") == Decimal("0.01")
    with pytest.raises(ValueError, match="at least 0.01"):
        parse("0.001")
