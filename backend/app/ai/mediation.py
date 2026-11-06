"""Mediation: options for members who are unpaid at the cycle deadline.

The options are deterministic and every one needs human approval. Mediation never executes
anything and takes no tool executor: the `MEDIATION` allow-list is empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from ..schemas.contracts import AgentName, MediationKind, MediationOption, MemberId
from .guardrails import build_decision
from .ids import new_id
from .inputs import CycleDue, MemberDue
from .llm import JsonModel
from .prompts.mediation import TEMPLATES

__all__ = ["REQUIRED_APPROVERS", "propose_mediation"]

TEMPLATE_MODEL = "template"
MAX_NAME_CHARS = 80

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


async def propose_mediation(
    cycle: CycleDue,
    unpaid: Sequence[MemberDue],
    *,
    json_models: Sequence[JsonModel] | None = None,
) -> dict[MemberId, list[MediationOption]]:
    """Options per unpaid member, in the order partial payment, swap, reserve cover."""
    del json_models  # wired to the description call in the next change
    result: dict[MemberId, list[MediationOption]] = {}
    for member in unpaid:
        if member.unpaid_amount <= 0:
            continue
        options: list[MediationOption] = []
        for kind in _eligible_kinds(member, cycle):
            options.append(
                MediationOption(
                    option_id=new_id("med"),
                    kind=kind,
                    description=_template(kind, member),
                    amount=member.unpaid_amount if kind is MediationKind.RESERVE_COVER else None,
                    agent_decision=build_decision(
                        AgentName.MEDIATION,
                        circle_id=cycle.circle_id,
                        cycle_index=cycle.cycle_index,
                        model=TEMPLATE_MODEL,
                        rationale=_RULES[kind],
                    ),
                )
            )
        result[member.member_id] = options
    return result
