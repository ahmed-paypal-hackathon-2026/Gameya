"""Interface-2 circle routes, M1 fixture-backed (WP-1.1). WP-1.3 swaps the engine in."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from ulid import ULID

from ..ai.circle_agent import CircleCreationFailed, circle_agent_turn, paypal_references
from ..ai.toolkit_adapter import ToolExecutor
from ..auth import actor_from_request, require_organiser
from ..db import get_db
from ..schemas.contracts import (
    AuditResponse,
    CircleAgentChatRequest,
    CircleAgentChatResponse,
    CircleCreateResponse,
    CircleDetail,
    CircleId,
    CopilotAnswer,
    CopilotQuestionRequest,
    CreateCircleRequest,
    JoinCircleRequest,
    JoinCircleResponse,
    LedgerResponse,
    MemberId,
    NeedExtraction,
    NeedSubmissionRequest,
    OrderApprovalRequest,
    OrderProposal,
    OrderProposeRequest,
    ReliabilityResponse,
    StressTestRequest,
    StressTestResult,
    SwapApprovalRequest,
    SwapCreateRequest,
    SwapProposal,
)
from ..services.circle_data import circle_detail, create_circle_rows, record_decision
from ..services.paypal.executor import get_tool_executor
from .fixtures import fixture
from .leash import Actor

# Reliability, ledger and audit are organiser-only (plan RSK-09): a lateness score
# must never be readable by other members or by anonymous visitors.
OrganiserActor = Annotated[Actor, Depends(actor_from_request)]
LoggedIn = Annotated[Actor, Depends(actor_from_request)]
DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/api/v1/circles", tags=["circles"])
agent_router = APIRouter(prefix="/api/v1", tags=["agent"])

DEMO_HEADER = {"X-Demo-Fixture": "1"}  # transparency: M1 serves the golden story


@router.post("", response_model=CircleCreateResponse)
async def create_circle(
    body: CreateCircleRequest,
    actor: LoggedIn,
    db: DbSession,
    executor: Annotated[ToolExecutor, Depends(get_tool_executor)],
) -> CircleCreateResponse | JSONResponse:
    """The organiser confirmed the card: create the PayPal product + plan, then store the circle.

    The organiser is the logged-in member, never the `organiser_id` the client sent.
    Nothing is stored unless both PayPal objects were created.
    """
    circle_id = f"circle-{str(ULID()).lower()}"
    spec = body.spec.model_copy(update={"organiser_id": MemberId(actor.user_id)})
    try:
        turn = await circle_agent_turn(
            [],
            True,
            confirmed_spec=spec,
            executor=executor,
            reserve_pct=body.reserve_pct,
            circle_id=CircleId(circle_id),
        )
    except CircleCreationFailed as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "PAYPAL_ERROR",
                    "message": "PayPal could not create the circle's plan. Nothing was saved.",
                    "details": {"step": exc.step},
                }
            },
        )
    refs = paypal_references(turn)
    create_circle_rows(
        db,
        circle_id=circle_id,
        spec=spec,
        organiser_id=actor.user_id,
        reserve_pct=body.reserve_pct,
        product_id=refs.product_id,
        plan_id=refs.plan_id,
    )
    if turn.agent_decision is not None:
        record_decision(db, turn.agent_decision, actor.user_id)
    detail = circle_detail(db, circle_id)
    assert detail is not None  # just written in this session
    return CircleCreateResponse(circle=detail, paypal=refs)


@router.get("/{circle_id}", response_model=CircleDetail)
def get_circle(circle_id: str, response: Response, db: DbSession) -> CircleDetail:
    detail = circle_detail(db, circle_id)
    if detail is not None:
        return detail
    response.headers.update(DEMO_HEADER)  # not in the database yet: the golden story
    return CircleDetail.model_validate(fixture("circle-get"))


@router.post("/{circle_id}/join", response_model=JoinCircleResponse)
def join_circle(circle_id: str, body: JoinCircleRequest, response: Response) -> JoinCircleResponse:
    response.headers.update(DEMO_HEADER)
    return JoinCircleResponse.model_validate(fixture("circle-join"))


@router.post("/{circle_id}/needs", response_model=NeedExtraction)
def submit_need(circle_id: str, body: NeedSubmissionRequest, response: Response) -> NeedExtraction:
    response.headers.update(DEMO_HEADER)
    return NeedExtraction.model_validate(fixture("circle-needs"))


@router.post("/{circle_id}/order/propose", response_model=OrderProposal)
def propose_order(circle_id: str, body: OrderProposeRequest, response: Response) -> OrderProposal:
    response.headers.update(DEMO_HEADER)
    return OrderProposal.model_validate(fixture("circle-order-propose"))


@router.post("/{circle_id}/order/approve", response_model=OrderProposal)
def approve_order(circle_id: str, body: OrderApprovalRequest, response: Response) -> OrderProposal:
    response.headers.update(DEMO_HEADER)
    return OrderProposal.model_validate(fixture("circle-order-propose"))


@router.post("/{circle_id}/copilot", response_model=CopilotAnswer)
def copilot(circle_id: str, body: CopilotQuestionRequest, response: Response) -> CopilotAnswer:
    response.headers.update(DEMO_HEADER)
    return CopilotAnswer.model_validate(fixture("circle-copilot"))


@router.post("/{circle_id}/swaps", response_model=SwapProposal)
def request_swap(circle_id: str, body: SwapCreateRequest, response: Response) -> SwapProposal:
    response.headers.update(DEMO_HEADER)
    return SwapProposal.model_validate(fixture("circle-swaps"))


@router.post("/{circle_id}/swaps/{swap_id}/approve", response_model=SwapProposal)
def approve_swap(
    circle_id: str, swap_id: str, body: SwapApprovalRequest, response: Response
) -> SwapProposal:
    response.headers.update(DEMO_HEADER)
    return SwapProposal.model_validate(fixture("circle-swaps"))


@router.post("/{circle_id}/stress-test", response_model=StressTestResult)
def stress_test(circle_id: str, body: StressTestRequest, response: Response) -> StressTestResult:
    response.headers.update(DEMO_HEADER)
    return StressTestResult.model_validate(fixture("circles-stress-test"))


@router.get("/{circle_id}/ledger", response_model=LedgerResponse)
def ledger(circle_id: str, response: Response, actor: OrganiserActor) -> LedgerResponse:
    require_organiser(actor, circle_id)
    response.headers.update(DEMO_HEADER)
    return LedgerResponse.model_validate(fixture("circle-ledger"))


@router.get("/{circle_id}/audit", response_model=AuditResponse)
def audit(circle_id: str, response: Response, actor: OrganiserActor) -> AuditResponse:
    require_organiser(actor, circle_id)
    response.headers.update(DEMO_HEADER)
    return AuditResponse.model_validate(fixture("circle-audit"))


@router.get("/{circle_id}/reliability", response_model=ReliabilityResponse)
def reliability(circle_id: str, response: Response, actor: OrganiserActor) -> ReliabilityResponse:
    require_organiser(actor, circle_id)
    response.headers.update(DEMO_HEADER)
    return ReliabilityResponse.model_validate(fixture("circle-reliability"))


@agent_router.post("/agent/circle/chat", response_model=CircleAgentChatResponse)
async def agent_chat(body: CircleAgentChatRequest, actor: LoggedIn) -> CircleAgentChatResponse:
    """Fill the circle card from the conversation. This route never calls PayPal:
    confirming the card is a separate, explicit POST /circles by the organiser."""
    if body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="confirm the circle card with POST /api/v1/circles",
        )
    turn = await circle_agent_turn(body.history, False)
    if turn.spec is not None:
        turn = turn.model_copy(
            update={"spec": turn.spec.model_copy(update={"organiser_id": MemberId(actor.user_id)})}
        )
    return CircleAgentChatResponse(
        session_id=body.session_id or f"chat-{str(ULID()).lower()}", turn=turn
    )
