"""Mediation: deterministic options, approval requirements, templates."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest
from app.ai.inputs import CycleDue, MemberDue
from app.ai.mediation import REQUIRED_APPROVERS, propose_mediation
from app.schemas.contracts import AgentName, CircleId, MediationKind, MemberId
from pydantic import ValidationError

from .fakes import FakeJsonModel  # noqa: F401  (used by the LLM tests below)

OMAR = MemberId("mem_omar")


def member(**over: Any) -> MemberDue:
    data: dict[str, Any] = {
        "member_id": OMAR,
        "name": "Omar",
        "email": "omar@example.com",
        "locale": "en",
        "slot_index": 2,
        "has_received_pot": False,
        "contribution_id": "con_01JC4Q8ZK3",
        "unpaid_amount": "100.00",
    }
    data.update(over)
    return MemberDue.model_validate(data)


def cycle(**over: Any) -> CycleDue:
    data: dict[str, Any] = {
        "cycle_index": 3,
        "circle_id": CircleId("cir_test"),
        "circle_name": "Family Circle",
        "member_count": 6,
        "due_at": "2026-11-10T12:00:00+00:00",
        "contribution_amount": "100.00",
        "reserve_balance": "30.00",
    }
    data.update(over)
    return CycleDue.model_validate(data)


async def test_options_order_and_eligibility() -> None:
    result = await propose_mediation(cycle(), [member()], json_models=[])
    assert [o.kind for o in result[OMAR]] == [MediationKind.PARTIAL_NOW, MediationKind.SWAP]


async def test_reserve_cover_when_reserve_sufficient() -> None:
    result = await propose_mediation(cycle(reserve_balance="100.00"), [member()], json_models=[])
    options = result[OMAR]
    assert [o.kind for o in options] == [
        MediationKind.PARTIAL_NOW,
        MediationKind.SWAP,
        MediationKind.RESERVE_COVER,
    ]
    cover = options[2]
    assert cover.amount == Decimal("100.00")
    assert REQUIRED_APPROVERS[MediationKind.RESERVE_COVER] == ("organiser",)


async def test_no_reserve_cover_when_reserve_short_by_a_cent() -> None:
    result = await propose_mediation(cycle(reserve_balance="99.99"), [member()], json_models=[])
    assert MediationKind.RESERVE_COVER not in [o.kind for o in result[OMAR]]


async def test_no_swap_after_receiving_pot() -> None:
    result = await propose_mediation(cycle(), [member(has_received_pot=True)], json_models=[])
    assert [o.kind for o in result[OMAR]] == [MediationKind.PARTIAL_NOW]


async def test_no_swap_in_last_slot() -> None:
    result = await propose_mediation(cycle(), [member(slot_index=5)], json_models=[])
    assert MediationKind.SWAP not in [o.kind for o in result[OMAR]]


async def test_amount_is_none_except_reserve_cover() -> None:
    result = await propose_mediation(cycle(reserve_balance="100.00"), [member()], json_models=[])
    for option in result[OMAR]:
        if option.kind is MediationKind.RESERVE_COVER:
            assert option.amount == Decimal("100.00")
        else:
            assert option.amount is None


async def test_every_option_needs_approval_and_has_a_decision() -> None:
    result = await propose_mediation(cycle(reserve_balance="100.00"), [member()], json_models=[])
    ids = set()
    for option in result[OMAR]:
        assert option.requires_approval is True
        assert option.option_id.startswith("med_")
        assert option.agent_decision is not None
        assert option.agent_decision.agent is AgentName.MEDIATION
        assert option.agent_decision.circle_id == "cir_test"
        assert option.agent_decision.cycle_index == 3
        assert option.agent_decision.tool_calls == []
        assert option.agent_decision.model == "template"
        ids.add(option.option_id)
    assert len(ids) == 3
    with pytest.raises(ValidationError):
        result[OMAR][0].requires_approval = False  # frozen field


def test_required_approvers_table_is_read_only() -> None:
    assert isinstance(REQUIRED_APPROVERS, MappingProxyType)
    assert REQUIRED_APPROVERS[MediationKind.PARTIAL_NOW] == ("member", "organiser")
    assert REQUIRED_APPROVERS[MediationKind.SWAP] == ("member", "counterparty")
    assert REQUIRED_APPROVERS[MediationKind.RESERVE_COVER] == ("organiser",)
    with pytest.raises(TypeError):
        REQUIRED_APPROVERS[MediationKind.SWAP] = ("x",)  # type: ignore[index]


async def test_template_descriptions_exact() -> None:
    result = await propose_mediation(cycle(reserve_balance="100.00"), [member()], json_models=[])
    descriptions = {o.kind: o.description for o in result[OMAR]}
    assert descriptions[MediationKind.PARTIAL_NOW] == (
        "Omar pays part of the $100.00 now and the rest before the payout."
    )
    assert descriptions[MediationKind.SWAP] == (
        "Omar moves to a later payout slot to get more time to pay."
    )
    assert descriptions[MediationKind.RESERVE_COVER] == (
        "The circle reserve covers Omar's $100.00 now; Omar repays the reserve later."
    )


async def test_empty_unpaid_returns_empty() -> None:
    assert await propose_mediation(cycle(), [], json_models=[]) == {}


async def test_zero_unpaid_member_gets_no_entry() -> None:
    result = await propose_mediation(cycle(), [member(unpaid_amount="0.00")], json_models=[])
    assert result == {}
