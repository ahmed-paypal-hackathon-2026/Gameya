"""TST-17: LLM outage suite.

With the model down, slow, erroring or answering garbage, every agent still returns a valid
result from its template or deterministic path, quickly.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import pytest
from app.ai import circle_agent, mediation, smart_chase
from app.ai.llm import ToolCall
from app.ai.mediation import propose_mediation
from app.ai.smart_chase import execute_chase, plan_chase
from app.schemas.contracts import (
    ChaseAction,
    ChatMessage,
    ChatRole,
    CircleId,
    CircleSpec,
    MediationKind,
    MemberId,
    ReliabilityScore,
)
from pydantic import BaseModel

from .fakes import FakeExecutor
from .test_injection_suite import BASE_CHAT, _cycle, _member

M = TypeVar("M", bound=BaseModel)

MAX_SECONDS = 0.35  # below the 0.4 s transient-retry sleep, above CI jitter
# Short enough that the failover loop does not sleep before its transient retry (0.4 s).
SHORT_BUDGET = 0.85
DUE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TODAY = date(2026, 10, 28)
MODES = ["timeout", "http_503", "malformed", "empty"]


class ProviderError(Exception):
    """An SDK-style error carrying an HTTP status in `code`."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class OutageModel:
    """A model that is down in one of four ways. Satisfies both model protocols."""

    def __init__(self, mode: str, name: str = "outage") -> None:
        self.name = name
        self.mode = mode
        self.calls = 0

    def _fail(self) -> None:
        if self.mode == "timeout":
            raise TimeoutError
        if self.mode == "http_503":
            raise ProviderError(503, "The model is overloaded. Please try again later.")
        if self.mode == "malformed":
            json.loads("{not json")

    async def generate_json(self, system_prompt: str, user_prompt: str, schema: type[M]) -> M:
        self.calls += 1
        self._fail()
        empty: dict[str, Any] = {"items": []} if "items" in schema.model_fields else {}
        if "text" in schema.model_fields:
            empty = {"text": ""}
        return schema.model_validate(empty)

    async def generate_tool_calls(
        self,
        system_prompt: str,
        user_prompt: str,
        declarations: Sequence[dict[str, Any]],
        allowed: Sequence[str],
    ) -> list[ToolCall]:
        self.calls += 1
        self._fail()
        return []


def _chain(mode: str) -> list[OutageModel]:
    return [OutageModel(mode, "primary"), OutageModel(mode, "lite")]


@pytest.fixture(autouse=True)
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (circle_agent, smart_chase, mediation):
        monkeypatch.setattr(module, "LLM_BUDGET_SECONDS", SHORT_BUDGET)


def _score() -> ReliabilityScore:
    return ReliabilityScore(member_id=MemberId("mem_omar"), p_late=0.9)


@pytest.mark.parametrize("mode", MODES)
async def test_circle_agent_survives_outage(mode: str) -> None:
    history = [ChatMessage(role=ChatRole.USER, content=BASE_CHAT)]
    models = _chain(mode)
    started = time.perf_counter()
    turn = await circle_agent.circle_agent_turn(history, False, today=TODAY, json_models=models)
    chat_seconds = time.perf_counter() - started

    assert models[0].calls == 1, "the model was actually tried"
    assert chat_seconds < MAX_SECONDS
    assert turn.spec is not None and turn.needs_confirmation
    assert turn.spec.member_count == 6
    assert turn.spec.contribution_amount == Decimal("100.00")
    assert turn.agent_decision is not None
    assert turn.agent_decision.model in {"template", "deterministic"}

    spec = CircleSpec.model_validate(turn.spec.model_dump())
    executor = FakeExecutor()
    plan_models = _chain(mode)
    started = time.perf_counter()
    confirmed = await circle_agent.circle_agent_turn(
        history,
        True,
        confirmed_spec=spec,
        executor=executor,
        plan_models=plan_models,
        circle_id=CircleId("cir_out"),
    )
    confirmed_seconds = time.perf_counter() - started

    assert plan_models[0].calls == 1
    assert confirmed_seconds < MAX_SECONDS
    assert [c[0] for c in executor.calls] == ["create_product", "create_subscription_plan"]
    assert confirmed.agent_decision is not None
    assert confirmed.agent_decision.model in {"template", "deterministic"}
    refs = circle_agent.paypal_references(confirmed)
    assert refs.product_id and refs.plan_id


@pytest.mark.parametrize("kind", ["nudge", "invoice", "reminder"])
@pytest.mark.parametrize("mode", MODES)
async def test_smart_chase_survives_outage(mode: str, kind: str) -> None:
    unpaid = "100.00"
    extra: dict[str, Any] = {"open_invoice_id": "INV2-ABCD"} if kind == "reminder" else {}
    member = _member("Omar", "en", unpaid, **extra)
    now = DUE - timedelta(days=2) if kind == "nudge" else DUE + timedelta(days=2)
    models = _chain(mode)

    started = time.perf_counter()
    plan = await plan_chase(member, _cycle(), _score(), now=now, json_models=models)
    seconds = time.perf_counter() - started

    assert models[0].calls == 1, "the model was actually tried"
    assert seconds < MAX_SECONDS
    assert (
        plan.action
        is {
            "nudge": ChaseAction.NUDGE,
            "invoice": ChaseAction.INVOICE,
            "reminder": ChaseAction.REMINDER,
        }[kind]
    )
    assert plan.message_source == "template"
    assert plan.agent_decision is not None
    assert plan.agent_decision.model == "template"
    text = plan.nudge_text or plan.invoice_note
    assert text and "$100.00" in text
    assert plan.recipient_email == "omar@example.com"

    if kind != "nudge":
        executor = FakeExecutor(results={"generate_invoice_qr_code": {"qr_code_base64": "iVBOR="}})
        result = await execute_chase(plan, executor=executor)
        assert result.invoice_id
        assert result.decision.model == "template"


@pytest.mark.parametrize("mode", MODES)
async def test_mediation_survives_outage(mode: str) -> None:
    members = [
        _member("Omar", "en", "100.00"),
        _member("Sara", "en", "100.00", member_id=MemberId("mem_sara"), slot_index=3),
    ]
    models = _chain(mode)

    started = time.perf_counter()
    result = await propose_mediation(_cycle(reserve_balance="100.00"), members, json_models=models)
    seconds = time.perf_counter() - started

    assert models[0].calls == 1, "one model call for every option of every member"
    assert seconds < MAX_SECONDS
    assert set(result) == {MemberId("mem_omar"), MemberId("mem_sara")}
    for options in result.values():
        assert [o.kind for o in options] == [
            MediationKind.PARTIAL_NOW,
            MediationKind.SWAP,
            MediationKind.RESERVE_COVER,
        ]
        for option in options:
            assert option.requires_approval is True
            assert option.description
            assert option.agent_decision is not None
            assert option.agent_decision.model in {"template", "deterministic"}
        assert options[2].amount == Decimal("100.00")
