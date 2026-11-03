"""DEMO_MODE-only admin routes. Engine-backed fast-forward/reset land with WP-1.3."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..config import get_settings
from ..schemas.contracts import FastForwardRequest, ResetDemoResponse

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/cycles/{cycle_id}/fast-forward")
def fast_forward(cycle_id: str, body: FastForwardRequest, response: Response) -> dict[str, object]:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="admin routes only exist in DEMO_MODE")
    response.headers.update({"X-Demo-Fixture": "1"})
    return {"fast_forwarded": True, "cycle_id": cycle_id, "to_cycle_index": body.to_cycle_index}


@router.post("/reset-demo", response_model=ResetDemoResponse)
def reset_demo(response: Response) -> ResetDemoResponse:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="admin routes only exist in DEMO_MODE")
    response.headers.update({"X-Demo-Fixture": "1"})
    # The golden story is the single demo circle; WP-1.3 re-seeds rows from scripts/seed_demo.
    return ResetDemoResponse.model_validate({"reset": True, "circle_id": "circle-golden-01"})
