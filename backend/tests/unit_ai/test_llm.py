"""Shared LLM layer: failover loop, daily-quota skipping, last-model budget, tool calls."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.ai._llm_port import LLM_TIMEOUT_S, LLMClient
from app.ai.llm import (
    DEFAULT_QUOTA_COOLDOWN_SECONDS,
    EmptyModelResponse,
    GeminiLLMClient,
    GeminiModel,
    JsonModel,
    LLMUnavailable,
    ToolCall,
    ToolPlanModel,
    daily_quota_reset_seconds,
    default_llm_client,
    default_models,
    gemini_models_from_env,
    is_quota_exhausted,
    is_transient_error,
    reset_quota_state,
    run_with_failover,
)
from app.ai.needs_extractor import extract_need_detailed
from app.schemas.contracts import CircleSnapshot
from pydantic import BaseModel

# Shape of the real Gemini error (abridged from a live 429 on the free tier).
GOOGLE_DAILY_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
    "quota ... limit: 20, model: gemini-3.6-flash\\nPlease retry in 8h10m21.0s.', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '29421s'}]}}"
)
GOOGLE_MINUTE_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'violations': [{'quotaId': "
    "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}, {'retryDelay': '20s'}]}}"
)


@pytest.fixture(autouse=True)
def _clean_quota_state() -> None:
    reset_quota_state()


class APIError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class Provider:
    def __init__(
        self,
        name: str,
        *,
        exc: BaseException | None = None,
        delay: float = 0.0,
        result: str = "ok",
    ) -> None:
        self.name = name
        self._exc = exc
        self._delay = delay
        self._result = result
        self.calls = 0
        self.timeouts: list[float] = []

    async def __call__(self, timeout: float) -> str:
        self.calls += 1
        self.timeouts.append(timeout)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return self._result


async def run(providers: list[Provider], **kwargs: float) -> str | None:
    async def attempt(p: Provider, timeout: float) -> str:
        return await p(timeout)

    options = {"budget_seconds": 2.0, "provider_timeout": 1.0, "retry_delay": 0.01}
    options.update(kwargs)
    return await run_with_failover(providers, attempt, purpose="test", **options)


# --- classification -------------------------------------------------------------------


def test_daily_quota_error_is_recognised_with_googles_retry_delay() -> None:
    assert daily_quota_reset_seconds(APIError(429, GOOGLE_DAILY_429)) == 29421.0


def test_daily_quota_without_retry_info_uses_default_cooldown() -> None:
    exc = APIError(429, "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    assert daily_quota_reset_seconds(exc) == DEFAULT_QUOTA_COOLDOWN_SECONDS


@pytest.mark.parametrize(
    "exc",
    [
        APIError(429, GOOGLE_MINUTE_429),
        APIError(503, "high demand"),
        APIError(400, GOOGLE_DAILY_429),
        RuntimeError(GOOGLE_DAILY_429),
    ],
)
def test_other_errors_are_not_daily_quota(exc: BaseException) -> None:
    assert daily_quota_reset_seconds(exc) is None


def test_daily_quota_is_not_retried_but_per_minute_limit_is() -> None:
    assert is_transient_error(APIError(429, GOOGLE_DAILY_429)) is False
    assert is_transient_error(APIError(429, GOOGLE_MINUTE_429)) is True


# --- quota-aware skipping -------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausted_model_is_tried_once_then_skipped_on_later_requests() -> None:
    out_of_quota = Provider("gemini:primary", exc=APIError(429, GOOGLE_DAILY_429))
    backup = Provider("gemini:backup")

    assert await run([out_of_quota, backup]) == "ok"
    assert (out_of_quota.calls, backup.calls) == (1, 1)  # no retry on daily quota
    assert is_quota_exhausted("gemini:primary")

    assert await run([out_of_quota, backup]) == "ok"
    assert (out_of_quota.calls, backup.calls) == (1, 2)  # skipped entirely


@pytest.mark.asyncio
async def test_skipping_costs_no_time() -> None:
    out_of_quota = Provider("gemini:primary", exc=APIError(429, GOOGLE_DAILY_429))
    await run([out_of_quota, Provider("gemini:backup")])
    start = time.perf_counter()
    await run([out_of_quota, Provider("gemini:backup")])
    assert time.perf_counter() - start < 0.05


@pytest.mark.asyncio
async def test_model_is_tried_again_after_the_reset_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_of_quota = Provider("gemini:primary", exc=APIError(429, GOOGLE_DAILY_429))
    await run([out_of_quota])
    assert out_of_quota.calls == 1

    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 29421 + 1)
    assert not is_quota_exhausted("gemini:primary")
    await run([out_of_quota])
    assert out_of_quota.calls == 2


@pytest.mark.asyncio
async def test_all_models_exhausted_returns_none_immediately() -> None:
    a = Provider("gemini:a", exc=APIError(429, GOOGLE_DAILY_429))
    b = Provider("gemini:b", exc=APIError(429, GOOGLE_DAILY_429))
    assert await run([a, b]) is None
    start = time.perf_counter()
    assert await run([a, b]) is None
    assert time.perf_counter() - start < 0.05
    assert (a.calls, b.calls) == (1, 1)


@pytest.mark.asyncio
async def test_exhaustion_is_tracked_per_model() -> None:
    await run([Provider("gemini:a", exc=APIError(429, GOOGLE_DAILY_429))])
    assert is_quota_exhausted("gemini:a")
    assert not is_quota_exhausted("gemini:b")


# --- budget for the last model --------------------------------------------------------


@pytest.mark.asyncio
async def test_last_model_gets_the_whole_remaining_budget() -> None:
    first = Provider("first", exc=APIError(400, "bad request"))
    slow_last = Provider("last", delay=0.3)
    result = await run([first, slow_last], budget_seconds=1.0, provider_timeout=0.1)
    assert result == "ok"
    assert slow_last.timeouts[0] > 0.5


@pytest.mark.asyncio
async def test_non_last_model_is_still_capped() -> None:
    slow_first = Provider("first", delay=0.3)
    backup = Provider("last")
    result = await run([slow_first, backup], budget_seconds=1.0, provider_timeout=0.1)
    assert result == "ok"
    assert slow_first.timeouts[0] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_model_before_an_exhausted_one_counts_as_last() -> None:
    await run([Provider("gemini:b", exc=APIError(429, GOOGLE_DAILY_429))])
    slow = Provider("gemini:a", delay=0.3)
    result = await run([slow, Provider("gemini:b")], budget_seconds=1.0, provider_timeout=0.1)
    assert result == "ok"
    assert slow.timeouts[0] > 0.5


@pytest.mark.asyncio
async def test_last_model_never_exceeds_the_total_budget() -> None:
    hang = Provider("only", delay=5.0)
    start = time.perf_counter()
    assert await run([hang], budget_seconds=0.2, provider_timeout=0.1) is None
    assert time.perf_counter() - start < 0.4


# --- Gemini function calling --------------------------------------------------------------

DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "create_invoice",
        "description": "Create a draft invoice.",
        "parameters": {
            "type": "object",
            "properties": {"currency_code": {"type": "string"}},
        },
    },
    {
        "name": "send_invoice",
        "description": "Send it.",
        "parameters": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
        },
    },
]
CREATE_ARGS: dict[str, Any] = {"currency_code": "USD"}


class FakeGenaiClient:
    """Stands in for ``google.genai.Client``; records the request config."""

    last_config: Any = None
    function_calls: Any = None

    def __init__(self, api_key: str) -> None:
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model: str, contents: str, config: Any) -> Any:
        FakeGenaiClient.last_config = config
        return SimpleNamespace(function_calls=FakeGenaiClient.function_calls)


@pytest.fixture
def fake_genai(monkeypatch: pytest.MonkeyPatch) -> type[FakeGenaiClient]:
    FakeGenaiClient.last_config = None
    FakeGenaiClient.function_calls = [
        SimpleNamespace(name="create_invoice", args=CREATE_ARGS),
        SimpleNamespace(name="send_invoice", args={"invoice_id": "x"}),
    ]
    monkeypatch.setattr("google.genai.Client", FakeGenaiClient)
    return FakeGenaiClient


@pytest.mark.asyncio
async def test_generate_tool_calls_returns_calls_in_order(
    fake_genai: type[FakeGenaiClient],
) -> None:
    calls = await GeminiModel("key", "gemini-test").generate_tool_calls(
        "system", "user", DECLARATIONS, ["create_invoice", "send_invoice"]
    )
    assert calls == [
        ToolCall("create_invoice", CREATE_ARGS),
        ToolCall("send_invoice", {"invoice_id": "x"}),
    ]


@pytest.mark.asyncio
async def test_generate_tool_calls_forces_function_mode(
    fake_genai: type[FakeGenaiClient],
) -> None:
    await GeminiModel("key", "gemini-test").generate_tool_calls(
        "system", "user", DECLARATIONS, ("create_invoice", "send_invoice")
    )
    calling = fake_genai.last_config.tool_config.function_calling_config
    assert str(getattr(calling.mode, "value", calling.mode)) == "ANY"
    assert calling.allowed_function_names == ["create_invoice", "send_invoice"]
    declared = fake_genai.last_config.tools[0].function_declarations
    assert [d.name for d in declared] == ["create_invoice", "send_invoice"]


@pytest.mark.asyncio
async def test_no_function_calls_raises_empty_model_response(
    fake_genai: type[FakeGenaiClient],
) -> None:
    fake_genai.function_calls = None
    with pytest.raises(EmptyModelResponse):
        await GeminiModel("key", "gemini-test").generate_tool_calls(
            "system", "user", DECLARATIONS, ["create_invoice"]
        )


@pytest.mark.asyncio
async def test_undeclared_allowed_name_is_rejected_before_any_call(
    fake_genai: type[FakeGenaiClient],
) -> None:
    with pytest.raises(ValueError, match="delete_invoice"):
        await GeminiModel("key", "gemini-test").generate_tool_calls(
            "system", "user", DECLARATIONS, ["create_invoice", "delete_invoice"]
        )
    assert fake_genai.last_config is None


# --- env / default models / protocols -----------------------------------------------------


def test_models_from_env_ignores_placeholder_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "your_key")
    assert gemini_models_from_env() == []
    assert default_models() == []


def test_models_from_env_uses_primary_then_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "real-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    assert gemini_models_from_env() == ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
    assert [m.name for m in default_models()] == [
        "gemini:gemini-3.5-flash",
        "gemini:gemini-3.1-flash-lite",
    ]


def test_gemini_model_satisfies_the_agent_protocols() -> None:
    model = GeminiModel("key", "gemini-test")
    as_json: JsonModel = model
    as_tools: ToolPlanModel = model
    assert as_json.name == as_tools.name == "gemini:gemini-test"


# --- schema JSON and plain text ------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"category": {"type": "string"}},
    "required": ["category"],
}


class TextGenaiClient:
    """Fake ``google.genai.Client`` whose reply depends on the requested model."""

    configs: list[Any] = []
    replies: dict[str, Any] = {}  # model -> text, or an Exception to raise
    models_called: list[str] = []

    def __init__(self, api_key: str) -> None:
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, *, model: str, contents: str, config: Any) -> Any:
        TextGenaiClient.configs.append(config)
        TextGenaiClient.models_called.append(model)
        reply = TextGenaiClient.replies.get(model, "")
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


@pytest.fixture
def text_genai(monkeypatch: pytest.MonkeyPatch) -> type[TextGenaiClient]:
    TextGenaiClient.configs = []
    TextGenaiClient.replies = {}
    TextGenaiClient.models_called = []
    monkeypatch.setattr("google.genai.Client", TextGenaiClient)
    return TextGenaiClient


@pytest.mark.asyncio
async def test_generate_schema_json_sends_json_schema_and_returns_dict(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["m"] = json.dumps({"category": "WEDDING"})
    out = await GeminiModel("key", "m").generate_schema_json("prompt", SCHEMA)
    assert out == {"category": "WEDDING"}
    config = text_genai.configs[0]
    assert config.response_json_schema == SCHEMA
    assert config.response_mime_type == "application/json"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "not json", "[1, 2]", '"text"', "null"])
async def test_generate_schema_json_rejects_empty_invalid_or_non_object(
    text_genai: type[TextGenaiClient], reply: str
) -> None:
    text_genai.replies["m"] = reply
    with pytest.raises((EmptyModelResponse, ValueError)):
        await GeminiModel("key", "m").generate_schema_json("prompt", SCHEMA)


@pytest.mark.asyncio
async def test_generate_text_returns_prose_and_rejects_empty(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["m"] = "  Hello there.  "
    assert await GeminiModel("key", "m").generate_text("prompt") == "Hello there."
    text_genai.replies["m"] = "   "
    with pytest.raises(EmptyModelResponse):
        await GeminiModel("key", "m").generate_text("prompt")


# --- GeminiLLMClient (the P3 port) ------------------------------------------------------------


def make_client(*names: str) -> GeminiLLMClient:
    return GeminiLLMClient([GeminiModel("key", n) for n in names])


def test_client_satisfies_the_p3_protocol_and_budget_is_under_p3_timeout() -> None:
    client: LLMClient = make_client("a")
    assert client.model_name == "gemini:a"
    assert GeminiLLMClient.BUDGET_SECONDS == 7.5 < LLM_TIMEOUT_S


@pytest.mark.asyncio
async def test_client_generate_json_and_text_report_the_answering_model(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["a"] = RuntimeError("down")
    text_genai.replies["b"] = json.dumps({"category": "RENT"})
    client = make_client("a", "b")
    assert client.model_name == "gemini:a"  # nothing has answered yet
    assert await client.generate_json("p", SCHEMA) == {"category": "RENT"}
    assert client.model_name == "gemini:b"
    text_genai.replies["b"] = "prose"
    assert await client.generate_text("p") == "prose"


@pytest.mark.asyncio
async def test_client_raises_unavailable_when_every_model_fails(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["a"] = RuntimeError("down")
    text_genai.replies["b"] = ""
    client = make_client("a", "b")
    with pytest.raises(LLMUnavailable):
        await client.generate_json("p", SCHEMA)
    with pytest.raises(LLMUnavailable):
        await client.generate_text("p")


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["[1]", "null", "plain words"])
async def test_client_raises_on_non_object_json_instead_of_returning_it(
    text_genai: type[TextGenaiClient], reply: str
) -> None:
    text_genai.replies["a"] = reply
    with pytest.raises(LLMUnavailable):
        await make_client("a").generate_json("p", SCHEMA)


@pytest.mark.asyncio
async def test_client_without_models_raises_unavailable() -> None:
    with pytest.raises(LLMUnavailable):
        await GeminiLLMClient([]).generate_json("p", SCHEMA)


def test_default_llm_client_is_none_without_a_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert default_llm_client() is None
    monkeypatch.setenv("GEMINI_API_KEY", "your_key")
    assert default_llm_client() is None
    monkeypatch.setenv("GEMINI_API_KEY", "real")
    assert isinstance(default_llm_client(), GeminiLLMClient)


# --- through Person 3's real consumer ------------------------------------------------------------

CIRCLE = CircleSnapshot(
    circle_id="c1",
    name="Circle",
    contribution_amount=Decimal(100),
    cycle_unit="MONTH",
    size_n=6,
    start_date=date(2026, 11, 1),
)
NEED_TEXT = "I need $600 for a wedding in March"
NEED_JSON = {
    "category": "WEDDING",
    "target_date": "2027-03-01",
    "amount_needed": "600",
    "urgency": 3,
    "flexibility": "low",
    "confidence": 0.9,
}


@pytest.mark.asyncio
async def test_p3_extractor_uses_the_gemini_client_on_success(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["a"] = json.dumps(NEED_JSON)
    result = await extract_need_detailed(NEED_TEXT, "en", CIRCLE, make_client("a"))
    assert result.source == "llm"
    assert result.model == "gemini:a"
    assert result.need.category == "WEDDING"


@pytest.mark.asyncio
async def test_p3_extractor_falls_back_to_rules_when_the_client_raises(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["a"] = RuntimeError("quota")
    result = await extract_need_detailed(NEED_TEXT, "en", CIRCLE, make_client("a"))
    assert result.source == "rules"
    assert any("LLMUnavailable" in n for n in result.notes)


# --- retry, timeout and failover policy ---------------------------------------------------


class FlakyProvider(Provider):
    """Raises ``exc`` for the first ``failures`` calls, then succeeds."""

    def __init__(self, name: str, exc: BaseException, failures: int) -> None:
        super().__init__(name, exc=exc)
        self._failures = failures

    async def __call__(self, timeout: float) -> str:
        self.calls += 1
        self.timeouts.append(timeout)
        if self.calls <= self._failures:
            raise self._exc  # type: ignore[misc]
        return self._result


@pytest.mark.asyncio
async def test_transient_429_retried_once() -> None:
    flaky = FlakyProvider("gemini:a", APIError(503, "high demand"), failures=1)
    assert await run([flaky]) == "ok"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_transient_error_retries_only_once_then_fails_over() -> None:
    always_503 = Provider("gemini:a", exc=APIError(503, "high demand"))
    backup = Provider("gemini:b")
    assert await run([always_503, backup]) == "ok"
    assert (always_503.calls, backup.calls) == (2, 1)


@pytest.mark.asyncio
async def test_per_minute_429_is_retried_once() -> None:
    flaky = FlakyProvider("gemini:a", APIError(429, GOOGLE_MINUTE_429), failures=1)
    assert await run([flaky]) == "ok"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_retry_skipped_when_budget_cannot_fit_it() -> None:
    always_503 = Provider("gemini:a", exc=APIError(503, "high demand"))
    # retry needs retry_delay + min_slot = 1.4 s, but only ~1.0 s of budget exists
    result = await run(
        [always_503], budget_seconds=1.0, provider_timeout=1.0, retry_delay=0.9, min_slot=0.5
    )
    assert result is None
    assert always_503.calls == 1


@pytest.mark.asyncio
async def test_timeout_not_retried() -> None:
    hang = Provider("gemini:a", delay=5.0)
    backup = Provider("gemini:b")
    assert await run([hang, backup], budget_seconds=2.0, provider_timeout=0.1) == "ok"
    assert (hang.calls, backup.calls) == (1, 1)


@pytest.mark.asyncio
async def test_non_transient_error_is_not_retried() -> None:
    bad = Provider("gemini:a", exc=APIError(400, "bad request"))
    assert await run([bad]) is None
    assert bad.calls == 1


@pytest.mark.asyncio
async def test_failover_uses_backup_after_primary_error() -> None:
    primary = Provider("gemini:a", exc=RuntimeError("boom"))
    backup = Provider("gemini:b", result="from-backup")
    assert await run([primary, backup]) == "from-backup"
    assert (primary.calls, backup.calls) == (1, 1)


@pytest.mark.asyncio
async def test_all_fail_returns_none() -> None:
    a = Provider("gemini:a", exc=RuntimeError("boom"))
    b = Provider("gemini:b", exc=RuntimeError("boom"))
    assert await run([a, b]) is None
    assert (a.calls, b.calls) == (1, 1)


@pytest.mark.asyncio
async def test_daily_quota_marks_and_skips_model() -> None:
    out = Provider("gemini:a", exc=APIError(429, GOOGLE_DAILY_429))
    assert await run([out]) is None
    assert is_quota_exhausted("gemini:a")
    assert await run([out]) is None
    assert out.calls == 1


# --- GeminiModel.generate_json (Pydantic schema class) -------------------------------------


class Verdict(BaseModel):
    category: str


@pytest.mark.asyncio
async def test_generate_json_validates_into_the_schema_class(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["m"] = json.dumps({"category": "RENT"})
    out = await GeminiModel("key", "m").generate_json("system", "user", Verdict)
    assert out == Verdict(category="RENT")
    config = text_genai.configs[0]
    assert config.system_instruction == "system"
    assert config.response_schema is Verdict


@pytest.mark.asyncio
async def test_generate_json_empty_response_raises(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["m"] = ""
    with pytest.raises(EmptyModelResponse):
        await GeminiModel("key", "m").generate_json("system", "user", Verdict)


@pytest.mark.asyncio
async def test_generate_json_invalid_payload_raises(
    text_genai: type[TextGenaiClient],
) -> None:
    text_genai.replies["m"] = json.dumps({"wrong": 1})
    with pytest.raises(ValueError):
        await GeminiModel("key", "m").generate_json("system", "user", Verdict)
