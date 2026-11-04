"""The LLM interface Person 3's modules depend on.

Person 2's ai/llm.py (Gemini fallback chain) satisfies this Protocol; until it
lands, callers pass `llm=None` and every P3 function uses its deterministic
fallback. Keeping the port tiny lets tests use an in-memory fake.
"""

from __future__ import annotations

import html
from typing import Any, Protocol

UNTRUSTED_TAG = "untrusted_member_text"
LLM_TIMEOUT_S = 8.0


class LLMClient(Protocol):
    model_name: str

    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return an object matching `schema`; raise on any failure."""
        ...

    async def generate_text(self, prompt: str) -> str:
        """Return plain prose; raise on any failure."""
        ...


def wrap_untrusted(text: str, max_chars: int = 1000) -> str:
    """Member free text as inert data: truncated, HTML-escaped, inside one tag pair.

    Escaping `<` and `>` means the member cannot close the tag early and inject
    instructions after it (plan §13.2).
    """
    return f"<{UNTRUSTED_TAG}>{html.escape(text[:max_chars], quote=False)}</{UNTRUSTED_TAG}>"
