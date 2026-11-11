"""Demo-login tokens and the authenticated actor (security fix for the WP-1.1 skeleton).

A token is "<persona_id>.<hmac>", signed with SESSION_SECRET, so a client cannot
forge or edit one. Organiser rights are looked up from server-side circle data,
never taken from anything the client sends. Real accounts are out of MVP scope;
WP-1.3 swaps the fixture lookup for Membership rows.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import cache

from fastapi import HTTPException, Request

from .api.fixtures import fixture
from .api.leash import Actor
from .config import get_settings
from .leash import LeashError
from .schemas.contracts import LeashErrorCode

_SEP = "."


def _signature(subject: str, secret: str) -> str:
    return hmac.new(secret.encode(), subject.encode(), hashlib.sha256).hexdigest()


def issue_token(persona_id: str, secret: str) -> str:
    return f"{persona_id}{_SEP}{_signature(persona_id, secret)}"


def verify_token(token: str, secret: str) -> str | None:
    """The persona id inside a valid token, or None for anything forged or malformed."""
    persona_id, sep, signature = token.rpartition(_SEP)
    if not sep or not persona_id or not signature:
        return None
    if not hmac.compare_digest(signature, _signature(persona_id, secret)):
        return None
    return persona_id


def member_id_for(persona_id: str) -> str:
    return "mem-" + persona_id.removeprefix("persona-")


@cache
def _organised_circles() -> dict[str, frozenset[str]]:
    """member_id -> circles they organise, from the server's own circle data."""
    circle = fixture("circle-get")
    organisers = {m["member_id"] for m in circle["members"] if m["role"] == "organiser"}
    return {member: frozenset({circle["circle_id"]}) for member in organisers}


def actor_for(persona_id: str) -> Actor:
    member = member_id_for(persona_id)
    return Actor(user_id=member, organiser_of=_organised_circles().get(member, frozenset()))


def require_organiser(actor: Actor, circle_id: str) -> None:
    """Raise Leash's ORGANISER_ONLY error unless the actor organises this circle."""
    if circle_id not in actor.organiser_of:
        raise LeashError(
            LeashErrorCode.ORGANISER_ONLY,
            "Only the circle organiser can do this.",
            {"circle_id": circle_id},
        )


def actor_from_request(request: Request) -> Actor:
    """FastAPI dependency: the actor proven by a Bearer token, else 401."""
    from .api.demo import PERSONAS  # local import: demo imports this module

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    persona_id = (
        verify_token(token.strip(), get_settings().session_secret)
        if scheme.lower() == "bearer"
        else None
    )
    if persona_id is None or persona_id not in PERSONAS:
        raise HTTPException(status_code=401, detail="a valid demo-login token is required")
    return actor_for(persona_id)
