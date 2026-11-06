"""Mediation: options for members who are unpaid at the cycle deadline.

The options are deterministic and every one needs human approval. The LLM only words the
descriptions, in a single call for all options of all members, and its text is number-checked
per option with a template fallback. Mediation never executes anything and takes no tool
executor: the `MEDIATION` allow-list is empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from pydantic import BaseModel

from ..schemas.contracts import AgentName, MediationKind, MediationOption, MemberId
from .guardrails import build_decision, prose_numbers_ok, wrap_untrusted
from .ids import new_id
from .inputs import CycleDue, MemberDue
from .llm import JsonModel, default_models, run_with_failover
from .prompts.mediation import SYSTEM_PROMPT, TEMPLATES

__all__ = [
    "REQUIRED_APPROVERS",
    "DescriptionItem",
    "MediationDescriptions",
    "propose_mediation",
]

TEMPLATE_MODEL = "template"
MAX_NAME_CHARS = 80
MAX_DESCRIPTION_CHARS = 300
LLM_BUDGET_SECONDS = 6.0
LLM_PROVIDER_TIMEOUT_SECONDS = 5.0

# Who must approve each kind. The contract option only carries `requires_approval=True`,
# so Person 1's approval endpoint reads the roles from here.
REQUIRED_APPROVERS: Mapping[MediationKind, tuple[str, ...]] = MappingProxyType(
    {
        MediationKind.PARTIAL_NOW: ("member", "organiser"),
        MediationKind.SWAP: ("member", "counterparty"),
        MediationKind.RESERVE_COVER: ("organiser",),
    }
)

_RULES: dict[MediationKind, str] = {
    MediationKind.PARTIAL_NOW: "Partial payment is always offered to an unpaid member.",
    MediationKind.SWAP: "Swap offered: pot not yet received and a later slot exists.",
    MediationKind.RESERVE_COVER: "Reserve cover offered: reserve balance covers the unpaid amount.",
}


class DescriptionItem(BaseModel):
    """One description, keyed by `{member_id}:{kind}` as listed in the prompt."""

    key: str
    text: str


class MediationDescriptions(BaseModel):
    items: list[DescriptionItem]


def _key(member: MemberDue, kind: MediationKind) -> str:
    return f"{member.member_id}:{kind.value}"


def _eligible_kinds(member: MemberDue, cycle: CycleDue) -> list[MediationKind]:
    kinds = [MediationKind.PARTIAL_NOW]
    if not member.has_received_pot and member.slot_index < cycle.member_count - 1:
        kinds.append(MediationKind.SWAP)
    if cycle.reserve_balance >= member.unpaid_amount:
        kinds.append(MediationKind.RESERVE_COVER)
    return kinds


def _template(kind: MediationKind, member: MemberDue) -> str:
    return TEMPLATES[kind].format(
        name=member.name[:MAX_NAME_CHARS], unpaid=f"{member.unpaid_amount:.2f}"
    )


async def _model_descriptions(
    cycle: CycleDue,
    plan: list[tuple[MemberDue, list[MediationKind]]],
    models: Sequence[JsonModel],
) -> tuple[dict[str, str], str] | None:
    """One LLM call for every option. Returns (text by key, model name), unchecked."""
    lines = [
        f"Circle reserve balance: ${cycle.reserve_balance:.2f}",
        "Options (write one description per key):",
    ]
    for member, kinds in plan:
        name = wrap_untrusted(member.name, max_chars=MAX_NAME_CHARS)
        for kind in kinds:
            lines.append(
                f"- key={_key(member, kind)} kind={kind.value} member={name} "
                f"unpaid=${member.unpaid_amount:.2f}"
            )
    prompt = "\n".join(lines)
    answered: list[str] = []

    async def attempt(model: JsonModel, _timeout: float) -> MediationDescriptions:
        result = await model.generate_json(SYSTEM_PROMPT, prompt, MediationDescriptions)
        answered.append(model.name)
        return result

    result = await run_with_failover(
        models,
        attempt,
        purpose="mediation.descriptions",
        budget_seconds=LLM_BUDGET_SECONDS,
        provider_timeout=LLM_PROVIDER_TIMEOUT_SECONDS,
    )
    if result is None:
        return None
    return {item.key: item.text.strip() for item in result.items}, answered[-1]


def _checked(text: str | None, member: MemberDue, cycle: CycleDue) -> str | None:
    """The model text if it is usable, else None (the caller then uses the template)."""
    if not text or len(text) > MAX_DESCRIPTION_CHARS:
        return None
    allowed = [f"{member.unpaid_amount:.2f}", f"{cycle.reserve_balance:.2f}"]
    return text if prose_numbers_ok(text, allowed) else None


async def propose_mediation(
    cycle: CycleDue,
    unpaid: Sequence[MemberDue],
    *,
    json_models: Sequence[JsonModel] | None = None,
) -> dict[MemberId, list[MediationOption]]:
    """Options per unpaid member, in the order partial payment, swap, reserve cover.

    Makes at most one LLM call, and none when no member has an option.
    """
    models = json_models if json_models is not None else default_models()
    plan = [
        (member, _eligible_kinds(member, cycle)) for member in unpaid if member.unpaid_amount > 0
    ]
    generated = await _model_descriptions(cycle, plan, models) if plan and models else None
    texts, model_name = generated if generated else ({}, TEMPLATE_MODEL)

    result: dict[MemberId, list[MediationOption]] = {}
    for member, kinds in plan:
        options: list[MediationOption] = []
        for kind in kinds:
            text = _checked(texts.get(_key(member, kind)), member, cycle)
            options.append(
                MediationOption(
                    option_id=new_id("med"),
                    kind=kind,
                    description=text if text is not None else _template(kind, member),
                    amount=member.unpaid_amount if kind is MediationKind.RESERVE_COVER else None,
                    agent_decision=build_decision(
                        AgentName.MEDIATION,
                        circle_id=cycle.circle_id,
                        cycle_index=cycle.cycle_index,
                        model=model_name if text is not None else TEMPLATE_MODEL,
                        rationale=_RULES[kind],
                    ),
                )
            )
        result[member.member_id] = options
    return result
