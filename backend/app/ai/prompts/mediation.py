"""Description copy for Mediation (plan §13.5)."""

from __future__ import annotations

from ...schemas.contracts import MediationKind

# `{name}` is the member's name, `{unpaid}` the 2-dp unpaid amount.
TEMPLATES: dict[MediationKind, str] = {
    MediationKind.PARTIAL_NOW: (
        "{name} pays part of the ${unpaid} now and the rest before the payout."
    ),
    MediationKind.SWAP: "{name} moves to a later payout slot to get more time to pay.",
    MediationKind.RESERVE_COVER: (
        "The circle reserve covers {name}'s ${unpaid} now; {name} repays the reserve later."
    ),
}
