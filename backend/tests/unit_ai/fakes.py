"""Test doubles for the AI lane: scripted tool executor and models."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from app.ai.llm import ToolCall
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class FakeExecutor:
    """Records calls, returns scripted dicts, and is idempotent per request id."""

    def __init__(
        self,
        results: Mapping[str, dict[str, Any]] | None = None,
        raises: Mapping[str, Exception] | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.raises = dict(raises or {})
        self.calls: list[tuple[str, BaseModel, str]] = []
        self._ids: dict[str, str] = {}

    async def call(self, tool: str, params: BaseModel, *, request_id: str) -> dict[str, Any]:
        self.calls.append((tool, params, request_id))
        if tool in self.raises:
            raise self.raises[tool]
        if tool in self.results:
            return dict(self.results[tool])
        if request_id not in self._ids:
            self._ids[request_id] = f"FAKE-{len(self._ids) + 1:08d}"
        return {"id": self._ids[request_id]}


class FakeJsonModel:
    """Satisfies `llm.JsonModel`: returns a scripted object or raises."""

    def __init__(
        self, result: BaseModel | None = None, error: Exception | None = None, name: str = "fake"
    ) -> None:
        self.name = name
        self._result = result
        self._error = error
        self.prompts: list[tuple[str, str]] = []

    async def generate_json(self, system_prompt: str, user_prompt: str, schema: type[M]) -> M:
        self.prompts.append((system_prompt, user_prompt))
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("FakeJsonModel has no scripted result")
        return schema.model_validate(self._result.model_dump())


class FakeToolPlanModel:
    """Satisfies `llm.ToolPlanModel`: returns scripted calls or raises."""

    def __init__(
        self,
        calls: Sequence[ToolCall] | None = None,
        error: Exception | None = None,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._calls = list(calls or [])
        self._error = error

    async def generate_tool_calls(
        self,
        system_prompt: str,
        user_prompt: str,
        declarations: Sequence[dict[str, Any]],
        allowed: Sequence[str],
    ) -> list[ToolCall]:
        if self._error is not None:
            raise self._error
        return list(self._calls)


class ObedientModel:
    """A model that does whatever the hostile text asks (the worst case for prompt injection).

    Through `generate_json` it answers every schema with the hostile text: a chase message,
    one description per `key=` (plus keys that were never offered), or a circle draft built
    from `draft`. Through `generate_tool_calls` it returns `tool_calls` whatever the prompt
    allowed, including forbidden tools, altered amounts and extra fields.
    """

    def __init__(
        self,
        text: str,
        *,
        draft: Mapping[str, Any] | None = None,
        tool_calls: Sequence[ToolCall] = (),
        name: str = "obedient",
    ) -> None:
        self.name = name
        self.text = text
        self.draft = dict(draft or {})
        self.tool_calls = list(tool_calls)
        self.user_prompts: list[str] = []
        self.tool_requests = 0

    async def generate_json(self, system_prompt: str, user_prompt: str, schema: type[M]) -> M:
        self.user_prompts.append(user_prompt)
        fields = schema.model_fields
        payload: dict[str, Any]
        if "items" in fields:
            keys = re.findall(r"key=(\S+)", user_prompt)
            members = {key.split(":")[0] for key in keys}
            every = keys + [
                f"{m}:{kind}" for m in members for kind in ("partial_now", "swap", "reserve_cover")
            ]
            payload = {"items": [{"key": key, "text": self.text} for key in dict.fromkeys(every)]}
            payload["items"].append({"key": "mem_nobody:reserve_cover", "text": self.text})
        elif "text" in fields:
            payload = {"text": self.text}
        else:
            payload = dict(self.draft)
        return schema.model_validate(payload)

    async def generate_tool_calls(
        self,
        system_prompt: str,
        user_prompt: str,
        declarations: Sequence[dict[str, Any]],
        allowed: Sequence[str],
    ) -> list[ToolCall]:
        self.tool_requests += 1
        return list(self.tool_calls)
