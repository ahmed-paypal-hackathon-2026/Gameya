"""Persona picker for the demo (P1 route, golden sandbox personas from WP-5.2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas.contracts import DemoLoginRequest, DemoLoginResponse, Persona
from .fixtures import fixture

router = APIRouter(prefix="/api/v1", tags=["auth"])

PERSONAS: dict[str, Persona] = {}


for _name in ("amina", "omar", "sara", "youssef", "lina", "karim"):
    _persona_id = f"persona-{_name}"
    PERSONAS[_persona_id] = Persona.model_validate(
        fixture("auth-demo-login")["persona"]
        if _name == "amina"
        else {"persona_id": _persona_id, "display_name": _name.capitalize(), "role": "member"}
    )


@router.post("/auth/demo-login", response_model=DemoLoginResponse)
def demo_login(body: DemoLoginRequest) -> DemoLoginResponse:
    persona = PERSONAS.get(body.persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="unknown persona")
    # Demo token = persona id; production auth is out of MVP scope.
    return DemoLoginResponse(persona=persona, token=body.persona_id)
