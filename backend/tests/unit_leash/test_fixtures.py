"""The shared frontend fixtures must validate against their contract models."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.leash.facts import PayoutFacts
from app.leash.templates import render_reason
from app.schemas.contracts import (
    GuardAction,
    LeashDecision,
    LeashDecisionRow,
    LeashInbox,
    LeashPolicy,
    LeashResolveResponse,
)
from pydantic import TypeAdapter

FIXTURES = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "json"
REASON = (
    "Held: Amina's payout email changed yesterday (rule PO-01). "
    "Confirm it with Amina before releasing $600.00."
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_leash_policy_fixture() -> None:
    policy = LeashPolicy.model_validate(load("leash_policy.json"))
    assert policy.circle_id == "cir_01JC4Q" and policy.version == 1
    assert policy.thresholds.high_risk_p_late == Decimal("0.50")


def test_leash_inbox_fixture() -> None:
    inbox = LeashInbox.model_validate(load("leash_inbox.json"))
    (item,) = inbox.items
    assert item.decision_id == "lsd_01JC9F" and item.subject_id == "pay_01JC9E"
    assert item.amount == Decimal("600.00") and item.rule_ids == ["PO-01"]
    assert item.recipient is not None and item.recipient.name == "Amina"
    assert item.reason == REASON and item.created_at.tzinfo is not None


def test_leash_decisions_fixture() -> None:
    rows = TypeAdapter(list[LeashDecisionRow]).validate_python(load("leash_decisions.json"))
    assert [r.decision_id for r in rows] == ["lsd_01JC9G", "lsd_01JC9F"]
    assert rows[0].created_at > rows[1].created_at
    assert rows[1].status == "APPROVED" and rows[1].reason == REASON


def test_leash_approve_fixture() -> None:
    resp = LeashResolveResponse.model_validate(load("leash_approve.json"))
    assert resp.decision_id == "lsd_01JC9G" and resp.approves == "lsd_01JC9F"
    assert resp.result is not None and resp.result["sender_batch_id"] == "gameya-pay_01JC9E"


def test_fixture_reason_is_what_leash_renders() -> None:
    facts = PayoutFacts.model_validate(
        {
            "now": datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
            "circle_id": "cir_01JC4Q",
            "payout_id": "pay_01JC9E",
            "amount": "600.00",
            "recipient_member_id": "mem_01JC4R",
            "recipient_name": "Amina",
            "recipient_payout_email": "amina.new@personal.example.com",
            "payer_emails": ["amina.new@personal.example.com"],
            "reserve_cover_used": False,
            "recipient_is_organiser": False,
            "recipient_has_unpaid": False,
            "payout_email_changed_at": datetime(2026, 10, 27, 9, 0, tzinfo=UTC),
        }
    )
    rendered = render_reason(GuardAction.RELEASE_POT_PAYOUT, LeashDecision.ASK, ["PO-01"], facts)
    assert rendered == REASON


@pytest.mark.parametrize(
    "name", ["leash_policy.json", "leash_inbox.json", "leash_decisions.json", "leash_approve.json"]
)
def test_fixture_files_are_utf8_json(name: str) -> None:
    assert (FIXTURES / name).read_bytes().decode("utf-8")
    assert load(name)
