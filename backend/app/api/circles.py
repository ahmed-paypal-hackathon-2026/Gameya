"""Interface-2 circle routes. Circle creation, needs, fair order, stress-test and
reliability are served by the engine from the database; the routes still marked
with X-Demo-Fixture serve the golden story until their task wires them."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from ulid import ULID

from ..ai.circle_agent import CircleCreationFailed, circle_agent_turn, paypal_references
from ..ai.fair_order import derive_seed, explain_order_detailed, propose_payout_order
from ..ai.guardrails import build_decision
from ..ai.llm import default_llm_client
from ..ai.needs_extractor import extract_need_detailed
from ..ai.toolkit_adapter import ToolExecutor
from ..auth import actor_from_request, require_organiser
from ..db import get_db
from ..ml.stress_test import explain_stress_test, run_stress_test
from ..schemas.contracts import (
    AgentName,
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
    ReliabilityRow,
    StressTestRequest,
    StressTestResult,
    SwapApprovalRequest,
    SwapCreateRequest,
    SwapProposal,
)
from ..services import circle_data
from ..services.circle_data import (
    active_members,
    circle_detail,
    circle_snapshot,
    create_circle_rows,
    is_member,
    list_needs,
    member_profiles,
    record_decision,
    reliability_scores,
    save_need,
    save_proposal,
    slot_dates,
)
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


def _require_member(db: Session, circle_id: str, actor: Actor) -> None:
    """404 for an unknown circle, 403 unless the actor has a seat in it."""
    if circle_snapshot(db, circle_id) is None:
        raise HTTPException(status_code=404, detail="circle not found")
    if not is_member(db, circle_id, actor.user_id):
        raise HTTPException(status_code=403, detail="you are not a member of this circle")


def _require_self(actor: Actor, member_id: str) -> None:
    """A member acts only for themselves: the body cannot name someone else."""
    if member_id != actor.user_id:
        raise HTTPException(status_code=403, detail="you can only act for yourself")


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
async def submit_need(
    circle_id: str, body: NeedSubmissionRequest, actor: LoggedIn, db: DbSession
) -> NeedExtraction:
    """Extract the member's need (LLM if configured, else the rules) and store it."""
    _require_self(actor, body.member_id)
    _require_member(db, circle_id, actor)
    snapshot = circle_snapshot(db, circle_id)
    assert snapshot is not None
    result = await extract_need_detailed(body.raw_text, body.locale, snapshot, default_llm_client())
    need = result.need.model_copy(update={"member_id": MemberId(actor.user_id)})
    save_need(db, circle_id, need, result.source)
    record_decision(
        db,
        build_decision(
            AgentName.NEEDS_EXTRACTOR,
            circle_id=circle_id,
            model=result.model or result.source,
            rationale=f"Extracted a {need.category.value} need for {actor.user_id}.",
        ),
        actor.user_id,
    )
    return need


@router.get("/{circle_id}/needs", response_model=list[NeedExtraction])
def get_needs(circle_id: str, actor: LoggedIn, db: DbSession) -> list[NeedExtraction]:
    """Every stored need in the circle (members see them on the order card)."""
    _require_member(db, circle_id, actor)
    return list_needs(db, circle_id)


@router.post("/{circle_id}/order/propose", response_model=OrderProposal)
async def propose_order(
    circle_id: str, body: OrderProposeRequest, actor: LoggedIn, db: DbSession
) -> OrderProposal:
    """The fair order from the optimiser. The request's seed is ignored: the tie-break
    seed is derived from the circle and its members, so nobody can steer ties."""
    _require_member(db, circle_id, actor)
    profiles = member_profiles(db, circle_id)
    slots = slot_dates(db, circle_id)
    if len(profiles) != len(slots):
        raise HTTPException(
            status_code=409,
            detail=f"{len(profiles)} active members for {len(slots)} cycles: fill every seat first",
        )
    seed = derive_seed(circle_id, [p.member.member_id for p in profiles])
    proposal = await asyncio.to_thread(propose_payout_order, profiles, slots, seed)
    explained = await explain_order_detailed(proposal, profiles, default_llm_client())
    proposal = proposal.model_copy(update={"explanations": explained.texts})
    save_proposal(db, circle_id, proposal)
    record_decision(
        db,
        build_decision(
            AgentName.FAIR_ORDER,
            circle_id=circle_id,
            model=explained.model or "hungarian+template",
            rationale=f"Proposed the payout order with tie-break seed {seed}; awaiting approvals.",
        ),
        actor.user_id,
    )
    return proposal


@router.post("/{circle_id}/order/approve", response_model=OrderProposal)
def approve_order(
    circle_id: str, body: OrderApprovalRequest, actor: LoggedIn, db: DbSession
) -> OrderProposal:
    """Record the member's approval; the order applies once every member approves."""
    _require_self(actor, body.member_id)
    _require_member(db, circle_id, actor)
    try:
        return circle_data.approve_order(db, circle_id, actor.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
async def stress_test(
    circle_id: str, body: StressTestRequest, actor: OrganiserActor, db: DbSession
) -> StressTestResult:
    """Monte Carlo completion odds per reserve % (organiser-only: built from P(late))."""
    require_organiser(actor, circle_id)
    snapshot = circle_snapshot(db, circle_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="circle not found")
    scores = list(reliability_scores(db, circle_id).values())
    if len(scores) != snapshot.size_n:
        raise HTTPException(status_code=409, detail="fill every seat before the stress-test")
    pcts = [Decimal(p) for p in body.reserve_pcts]
    try:
        result = await asyncio.to_thread(
            run_stress_test, snapshot, scores, pcts, body.n_sims, body.seed
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    locale = next(
        (m.locale for m in active_members(db, circle_id) if m.member_id == actor.user_id), "en"
    )
    text = await explain_stress_test(
        result, default_llm_client(), "ar-EG" if locale == "ar-EG" else "en", circle=snapshot
    )
    return result.model_copy(update={"explanation": text})


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
def reliability(circle_id: str, actor: OrganiserActor, db: DbSession) -> ReliabilityResponse:
    require_organiser(actor, circle_id)
    scores = reliability_scores(db, circle_id)
    return ReliabilityResponse(
        circle_id=CircleId(circle_id),
        rows=[
            ReliabilityRow(
                member_id=MemberId(m.member_id),
                display_name=m.name,
                score=scores[MemberId(m.member_id)],
            )
            for m in active_members(db, circle_id)
        ],
    )


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
