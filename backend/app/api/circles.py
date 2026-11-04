"""Interface-2 circle routes, M1 fixture-backed (WP-1.1). WP-1.3 swaps the engine in."""

from __future__ import annotations

from fastapi import APIRouter, Response

from ..schemas.contracts import (
    AuditResponse,
    CircleAgentChatRequest,
    CircleAgentChatResponse,
    CircleCreateResponse,
    CircleDetail,
    CopilotAnswer,
    CopilotQuestionRequest,
    CreateCircleRequest,
    JoinCircleRequest,
    JoinCircleResponse,
    LedgerResponse,
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
from .fixtures import fixture

router = APIRouter(prefix="/api/v1/circles", tags=["circles"])
agent_router = APIRouter(prefix="/api/v1", tags=["agent"])

DEMO_HEADER = {"X-Demo-Fixture": "1"}  # transparency: M1 serves the golden story


@router.post("", response_model=CircleCreateResponse)
def create_circle(body: CreateCircleRequest, response: Response) -> CircleCreateResponse:
    response.headers.update(DEMO_HEADER)
    return CircleCreateResponse.model_validate(fixture("circles-create"))


@router.get("/{circle_id}", response_model=CircleDetail)
def get_circle(circle_id: str, response: Response) -> CircleDetail:
    response.headers.update(DEMO_HEADER)
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
def ledger(circle_id: str, response: Response) -> LedgerResponse:
    response.headers.update(DEMO_HEADER)
    return LedgerResponse.model_validate(fixture("circle-ledger"))


@router.get("/{circle_id}/audit", response_model=AuditResponse)
def audit(circle_id: str, response: Response) -> AuditResponse:
    response.headers.update(DEMO_HEADER)
    return AuditResponse.model_validate(fixture("circle-audit"))


@router.get("/{circle_id}/reliability", response_model=ReliabilityResponse)
def reliability(circle_id: str, response: Response) -> ReliabilityResponse:
    response.headers.update(DEMO_HEADER)
    return ReliabilityResponse.model_validate(fixture("circle-reliability"))


@agent_router.post("/agent/circle/chat", response_model=CircleAgentChatResponse)
def agent_chat(body: CircleAgentChatRequest, response: Response) -> CircleAgentChatResponse:
    response.headers.update(DEMO_HEADER)
    return CircleAgentChatResponse.model_validate(fixture("agent-circle-chat"))
