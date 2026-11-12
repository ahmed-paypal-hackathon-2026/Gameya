"""Cycle routes: collect, sync, chase and close (engine-backed); pay-now and mediation
approvals still serve the golden story (X-Demo-Fixture).

Every money route is organiser-only. Collect, sync and close are deterministic engine
code; chase runs Smart Chase, whose PayPal calls go through its guarded allow-list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai.paypal_rest import PayPalToolError
from ..ai.toolkit_adapter import ToolExecutor
from ..auth import actor_from_request, require_organiser
from ..db import get_db
from ..db.models import Cycle
from ..domain import clock
from ..domain.circle_engine import InvariantViolation, close_cycle
from ..domain.collection import (
    CycleNotFound,
    CycleNotReady,
    chase_cycle,
    circle_id_of_cycle,
    open_collection,
    sync_collection,
    unpaid_member_ids,
)
from ..domain.locks import CycleBusy, cycle_lock
from ..schemas.contracts import MediationApproveRequest, PayNowRequest, PayNowResponse
from ..services.paypal.executor import get_tool_executor
from ..services.paypal.invoicing import (
    InvoiceGateway,
    InvoiceStatusError,
    get_invoice_gateway,
    get_token_provider,
)
from ..services.paypal.payouts import PayoutError
from .fixtures import fixture
from .leash import Actor

router = APIRouter(prefix="/api/v1/cycles", tags=["cycles"])

DEMO_HEADER = {"X-Demo-Fixture": "1"}
OrganiserActor = Annotated[Actor, Depends(actor_from_request)]
DbSession = Annotated[Session, Depends(get_db)]


def get_now() -> datetime:
    """FastAPI dependency: the engine clock (demo fast-forward can move it ahead)."""
    return clock.now()


class InvoiceSummary(BaseModel):
    member_id: str
    paypal_invoice_id: str | None
    status: str


class CollectResponse(BaseModel):
    cycle_id: str
    invoices: list[InvoiceSummary]


class SyncResponse(BaseModel):
    cycle_id: str
    newly_paid: int
    unpaid_member_ids: list[str]
    cycle_status: str


class ChaseActionSummary(BaseModel):
    member_id: str
    action: str
    invoice_id: str | None
    error: str | None = None


class ChaseResponse(BaseModel):
    cycle_id: str
    actions: list[ChaseActionSummary]


class CloseResponse(BaseModel):
    cycle_id: str
    payout_id: str
    sender_batch_id: str
    paypal_batch_id: str | None
    amount: str
    status: str


def _organiser_cycle(db: Session, cycle_id: str, actor: Actor) -> str:
    try:
        circle_id = circle_id_of_cycle(db, cycle_id)
    except CycleNotFound as exc:
        raise HTTPException(status_code=404, detail="cycle not found") from exc
    require_organiser(actor, circle_id)
    return circle_id


def _paypal_failed(step: str) -> HTTPException:
    return HTTPException(status_code=502, detail=f"PayPal {step} failed; nothing was marked paid")


def _busy(exc: CycleBusy) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/{cycle_id}/collect", response_model=CollectResponse)
async def collect(
    cycle_id: str,
    actor: OrganiserActor,
    db: DbSession,
    gateway: Annotated[InvoiceGateway, Depends(get_invoice_gateway)],
) -> CollectResponse:
    """Send every member this cycle's invoice (once; re-running is safe)."""
    _organiser_cycle(db, cycle_id, actor)
    try:
        async with cycle_lock(cycle_id):
            # Each sent invoice is committed as it goes, so a failure keeps the earlier ones.
            invoices = await open_collection(db, cycle_id, gateway)
    except CycleBusy as exc:
        raise _busy(exc) from exc
    except CycleNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PayPalToolError as exc:  # carries only status + debug id
        raise _paypal_failed("invoicing") from exc
    return CollectResponse(
        cycle_id=cycle_id,
        invoices=[
            InvoiceSummary(
                member_id=i.member_id, paypal_invoice_id=i.paypal_invoice_id, status=i.status
            )
            for i in invoices
        ],
    )


@router.post("/{cycle_id}/sync", response_model=SyncResponse)
async def sync(
    cycle_id: str,
    actor: OrganiserActor,
    db: DbSession,
    gateway: Annotated[InvoiceGateway, Depends(get_invoice_gateway)],
) -> SyncResponse:
    """Read invoice statuses from PayPal; paid invoices become paid contributions."""
    _organiser_cycle(db, cycle_id, actor)
    try:
        async with cycle_lock(cycle_id):
            newly_paid = await sync_collection(db, cycle_id, gateway)
    except CycleBusy as exc:
        raise _busy(exc) from exc
    except InvoiceStatusError as exc:
        raise _paypal_failed("invoice status") from exc
    cycle = db.get(Cycle, cycle_id)
    return SyncResponse(
        cycle_id=cycle_id,
        newly_paid=newly_paid,
        unpaid_member_ids=unpaid_member_ids(db, cycle_id),
        cycle_status=(cycle.status if cycle else "UPCOMING").lower(),
    )


@router.post("/{cycle_id}/chase", response_model=ChaseResponse)
async def chase(
    cycle_id: str,
    actor: OrganiserActor,
    db: DbSession,
    executor: Annotated[ToolExecutor, Depends(get_tool_executor)],
    now: Annotated[datetime, Depends(get_now)],
) -> ChaseResponse:
    """The organiser asks Smart Chase to follow up with everyone who still owes."""
    _organiser_cycle(db, cycle_id, actor)
    try:
        async with cycle_lock(cycle_id):
            outcomes = await chase_cycle(db, cycle_id, executor, now=now, actor=actor.user_id)
    except CycleBusy as exc:
        raise _busy(exc) from exc
    except CycleNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChaseResponse(
        cycle_id=cycle_id,
        actions=[
            ChaseActionSummary(
                member_id=o.member_id, action=o.action, invoice_id=o.invoice_id, error=o.error
            )
            for o in outcomes
        ],
    )


@router.post("/{cycle_id}/close", response_model=CloseResponse)
async def close(
    cycle_id: str,
    actor: OrganiserActor,
    db: DbSession,
    token_provider: Annotated[Callable[[], Awaitable[str]], Depends(get_token_provider)],
) -> CloseResponse:
    """Pay the pot to this cycle's approved recipient, once, if the ledger balances."""
    _organiser_cycle(db, cycle_id, actor)
    try:
        async with cycle_lock(cycle_id):
            payout = await close_cycle(db, cycle_id, token_provider=token_provider)
    except CycleBusy as exc:
        raise _busy(exc) from exc
    except InvariantViolation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PayoutError as exc:
        db.commit()  # keep the FAILED payout row: a retry reuses its sender_batch_id
        raise _paypal_failed("payout") from exc
    return CloseResponse(
        cycle_id=cycle_id,
        payout_id=payout.id,
        sender_batch_id=payout.sender_batch_id,
        paypal_batch_id=payout.paypal_batch_id,
        amount=f"{payout.amount:.2f}",
        status=payout.status,
    )


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
