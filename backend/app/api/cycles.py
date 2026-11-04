"""Cycle-level routes: pay-now (Orders v2 path) + mediation approvals (M1 fixtures)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from ..schemas.contracts import MediationApproveRequest, PayNowRequest, PayNowResponse
from .fixtures import fixture

router = APIRouter(prefix="/api/v1/cycles", tags=["cycles"])

DEMO_HEADER = {"X-Demo-Fixture": "1"}


@router.post("/{cycle_id}/pay-now", response_model=PayNowResponse)
def pay_now(cycle_id: str, body: PayNowRequest, response: Response) -> PayNowResponse:
    response.headers.update(DEMO_HEADER)
    return PayNowResponse.model_validate(fixture("cycles-pay-now"))


@router.post("/{cycle_id}/mediation/{option_id}/approve")
def approve_mediation(
    cycle_id: str, option_id: str, body: MediationApproveRequest, response: Response
) -> dict[str, object]:
    # Engine-backed state lands with WP-1.3; M1 confirms the human approval only.
    response.headers.update(DEMO_HEADER)
    return {"approved": True, "option_id": option_id, "cycle_id": cycle_id}
