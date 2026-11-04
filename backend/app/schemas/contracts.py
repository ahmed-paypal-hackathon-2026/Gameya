"""Canonical cross-team contract types for Gam'eya (plan Section 7.4).

Owned by Person 5. Any change needs a PR approved by Person 5 plus every
affected consumer lane, mirrored in frontend/src/types/contracts.ts.

Layout:
  - shared primitives   (Money / PositiveMoney / ids)
  - enums
  - Interface 1         typed Python models passed into the AI/ML functions
  - Interface 2         REST request/response models under /api/v1/*
  - Interface 3         Leash payout-guard types (Person 2)

Money is Decimal quantised to 0.01 ROUND_HALF_UP; floats are rejected on the
wire and in Python.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal, NewType, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

MemberId = NewType("MemberId", str)
CircleId = NewType("CircleId", str)


def quantize_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


CENT = Decimal("0.01")


def _reject_float(v: object) -> object:
    if isinstance(v, float):
        raise ValueError("floats are not allowed for money; send a string or Decimal")  # noqa: TRY004
    return v


def _quantise(v: Decimal) -> Decimal:
    if not v.is_finite():
        raise ValueError("money must be a finite amount")
    try:
        return v.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError("money amount is too large") from None


def _positive(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError("amount must be at least 0.01")
    return v


Money = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(_quantise)]
PositiveMoney = Annotated[Money, AfterValidator(_positive)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Environment(StrEnum):
    LOCAL_TEST = "local_test"
    SANDBOX = "sandbox"
    PRODUCTION = "production"


Locale = Literal["en", "ar-EG"]

LOCALES: tuple[str, ...] = get_args(Locale)


class Role(StrEnum):
    ORGANISER = "organiser"
    MEMBER = "member"


class CycleUnit(StrEnum):
    WEEK = "WEEK"
    MONTH = "MONTH"


class NeedCategory(StrEnum):
    WEDDING = "WEDDING"
    EDUCATION = "EDUCATION"
    MEDICAL = "MEDICAL"
    HOUSING = "HOUSING"
    BUSINESS = "BUSINESS"
    TRAVEL = "TRAVEL"
    OTHER = "OTHER"


class ContributionStatus(StrEnum):
    PENDING = "pending"
    INVOICED = "invoiced"
    PAID = "paid"
    COVERED = "covered"
    FAILED = "failed"


class SubscriptionStatus(StrEnum):
    NONE = "none"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


class CycleStatus(StrEnum):
    UPCOMING = "upcoming"
    DUE = "due"
    GRACE = "grace"
    READY = "ready"
    PAID_OUT = "paid_out"
    FAILED = "failed"


class PayoutStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID_OUT = "paid_out"
    FAILED = "failed"
    UNCLAIMED = "unclaimed"


class ContributionSource(StrEnum):
    SUBSCRIPTION = "subscription"
    ORDER = "order"
    INVOICE = "invoice"
    RESERVE = "reserve"


class MediationKind(StrEnum):
    PARTIAL_NOW = "partial_now"
    SWAP = "swap"
    RESERVE_COVER = "reserve_cover"


class AgentName(StrEnum):
    CIRCLE_AGENT = "circle_agent"
    SMART_CHASE = "smart_chase"
    MEDIATION = "mediation"
    COPILOT = "copilot"
    SWAP_BROKER = "swap_broker"
    NEEDS_EXTRACTOR = "needs_extractor"
    FAIR_ORDER = "fair_order"
    STRESS_TEST = "stress_test"
    LEASH = "leash"


class ToolName(StrEnum):
    CREATE_PRODUCT = "create_product"
    LIST_PRODUCTS = "list_products"
    SHOW_PRODUCT_DETAILS = "show_product_details"
    CREATE_SUBSCRIPTION_PLAN = "create_subscription_plan"
    LIST_SUBSCRIPTION_PLANS = "list_subscription_plans"
    SHOW_SUBSCRIPTION_PLAN_DETAILS = "show_subscription_plan_details"
    CREATE_SUBSCRIPTION = "create_subscription"
    SHOW_SUBSCRIPTION_DETAILS = "show_subscription_details"
    UPDATE_SUBSCRIPTION = "update_subscription"
    CANCEL_SUBSCRIPTION = "cancel_subscription"
    CREATE_INVOICE = "create_invoice"
    SEND_INVOICE = "send_invoice"
    SEND_INVOICE_REMINDER = "send_invoice_reminder"
    GENERATE_INVOICE_QR_CODE = "generate_invoice_qr_code"
    CREATE_ORDER = "create_order"
    GET_ORDER = "get_order"
    PAY_ORDER = "pay_order"
    LIST_TRANSACTIONS = "list_transactions"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class SwapStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    APPLIED = "applied"


class ChaseAction(StrEnum):
    NOOP = "noop"
    NUDGE = "nudge"
    INVOICE = "invoice"


class SanityStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class ToolCall(StrictModel):
    """An executed or proposed Agent Toolkit tool call, for the audit log."""

    name: ToolName
    arguments: dict[str, str | int | Decimal | None] = Field(default_factory=dict)
    result_summary: str | None = None


class ChatMessage(StrictModel):
    role: ChatRole
    content: str = Field(min_length=1, max_length=4000)
    created_at: datetime | None = None


class RiskFactor(StrictModel):
    key: str = Field(max_length=64)
    weight: float = Field(ge=-1.0, le=1.0)
    description: str = Field(max_length=200)


class ReliabilityFactor(StrictModel):
    feature: str
    contribution: float
    label: str


class ReliabilityScore(StrictModel):
    """Explainable P(late next cycle). Never framed as a credit score."""

    member_id: MemberId
    p_late: float = Field(ge=0.0, le=1.0)
    top_factors: list[ReliabilityFactor] = Field(max_length=3, default_factory=list)


class ReliabilityFeatures(StrictModel):
    """The payment-history factor set the reliability model is trained on."""

    on_time_ratio: float = Field(ge=0, le=1)
    avg_days_late: float = Field(ge=0)
    cycles_completed: int = Field(ge=0)
    failed_payments: int = Field(ge=0)
    invoices_needed: int = Field(ge=0)
    circles_completed: int = Field(ge=0)
    account_age_days: int = Field(ge=0)


class MemberSnapshot(StrictModel):
    member_id: MemberId
    display_name: str
    role: Role = Role.MEMBER
    locale: Locale = "en"
    slot_index: int | None = None
    subscription_status: SubscriptionStatus = SubscriptionStatus.NONE
    contribution_status: ContributionStatus | None = None
    days_late: int = Field(default=0, ge=0)
    p_late: float | None = Field(default=None, ge=0.0, le=1.0)


class CycleSnapshot(StrictModel):
    cycle_index: int = Field(ge=0)
    status: CycleStatus = CycleStatus.UPCOMING
    due_date: date
    amount_due: Money
    pot_total: Money
    recipient_id: MemberId | None = None
    unpaid_members: list[MemberSnapshot] = Field(default_factory=list)


class CircleSnapshot(StrictModel):
    """Read-only circle view built by Person 1 and consumed by the AI lanes."""

    circle_id: CircleId
    name: str
    contribution_amount: PositiveMoney
    currency: Literal["USD"] = "USD"
    cycle_unit: CycleUnit = CycleUnit.MONTH
    size_n: int = Field(ge=2, le=24)
    start_date: date
    reserve_pct: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100"))


class CircleSpec(StrictModel):
    """The typed result of the Circle Agent's slot-filling conversation."""

    name: str = Field(min_length=1, max_length=80)
    member_count: int = Field(ge=2, le=12)
    contribution_amount: PositiveMoney
    currency: Literal["USD"] = "USD"
    cycle_unit: CycleUnit = CycleUnit.MONTH
    start_date: date
    organiser_id: MemberId | None = None
    purpose: str | None = Field(default=None, max_length=500)
    confirmed: bool = False


class CircleDetail(StrictModel):
    """REST shape for GET /circles/{id} and POST /circles (spec + members + cycles)."""

    circle_id: CircleId
    spec: CircleSpec
    members: list[MemberSnapshot]
    cycles: list[CycleSnapshot]
    current_cycle_index: int = Field(ge=0, default=0)
    reserve_pct: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100"))


class AgentDecision(StrictModel):
    """One audit row per AI decision; nothing executes without approval."""

    decision_id: str
    agent: AgentName
    circle_id: CircleId | None = None
    cycle_index: int | None = None
    model: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    rationale: str
    created_at: datetime | None = None
    human_approved: bool = False


class CircleAgentTurn(StrictModel):
    reply: str
    spec: CircleSpec | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=5)
    agent_decision: AgentDecision | None = None
    needs_confirmation: bool = False


class NeedExtraction(StrictModel):
    """Structured output of the needs extractor (REQ-AI-02, Person 3)."""

    member_id: MemberId | None = None
    category: NeedCategory
    target_date: date | None = None
    amount_needed: Money | None = None
    urgency: int = Field(ge=1, le=5)
    flexibility: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    raw_month_hint: str | None = None


class MemberProfile(StrictModel):
    member: MemberSnapshot
    need: NeedExtraction | None = None
    reliability_p_late: float = Field(ge=0, le=1)


class SlotDate(StrictModel):
    slot_index: int = Field(ge=0)
    payout_date: date


class SlotAssignment(StrictModel):
    member_id: MemberId
    slot_index: int = Field(ge=0)
    payout_date: date
    cost: float


class OrderProposal(StrictModel):
    """Deterministic Hungarian-optimiser output; the LLM never picks the order."""

    assignments: list[SlotAssignment] = Field(min_length=1)
    seed: int
    explanations: dict[MemberId, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_member_per_slot(self) -> OrderProposal:
        slots = [a.slot_index for a in self.assignments]
        if len(slots) != len(set(slots)):
            raise ValueError("duplicate slot_index in OrderProposal")
        members = [a.member_id for a in self.assignments]
        if len(members) != len(set(members)):
            raise ValueError("duplicate member_id in OrderProposal")
        return self


class Viewer(StrictModel):
    viewer_id: MemberId
    role: Role
    circle_id: CircleId


class CircleReadTools(StrictModel):
    """SELECT-only ledger reads scoped to the viewer's own circle."""

    circle_id: CircleId
    allowed_queries: tuple[str, ...] = (
        "circle_summary",
        "cycle_status",
        "own_contribution_status",
        "upcoming_payout_date",
        "unpaid_members",
    )


class CopilotAnswer(StrictModel):
    answer: str
    engine_facts_only: bool = Field(default=True)
    support_rows: list[str] = Field(default_factory=list, max_length=10)
    agent_decision: AgentDecision | None = None


class ChasePlan(StrictModel):
    member_id: MemberId
    cycle_index: int = Field(ge=0)
    action: ChaseAction
    nudge_text: str | None = Field(default=None, max_length=1000)
    invoice_amount: PositiveMoney | None = None
    invoice_note: str | None = Field(default=None, max_length=1000)
    agent_decision: AgentDecision | None = None

    @model_validator(mode="after")
    def _shape(self) -> ChasePlan:
        if self.action is ChaseAction.NUDGE and not self.nudge_text:
            raise ValueError("nudge action requires nudge_text")
        if self.action is ChaseAction.INVOICE:
            if self.invoice_amount is None:
                raise ValueError("invoice action requires invoice_amount")
            if self.invoice_note is None:
                raise ValueError("invoice action requires invoice_note")
        return self

    def assert_within_contribution(self, contribution_amount: Decimal) -> None:
        """Guarantee an invoice never exceeds the circle contribution amount."""
        if self.invoice_amount is None:
            return
        if quantize_amount(self.invoice_amount) > quantize_amount(contribution_amount):
            raise ValueError(
                f"invoice_amount {self.invoice_amount} exceeds contribution {contribution_amount}"
            )


class MediationOption(StrictModel):
    option_id: str
    kind: MediationKind
    description: str = Field(max_length=500)
    amount: PositiveMoney | None = None
    requires_approval: bool = Field(default=True, frozen=True)
    agent_decision: AgentDecision | None = None


class SwapRequest(StrictModel):
    request_id: str
    requester_id: MemberId
    requester_slot_index: int = Field(ge=0)
    target_slot_index: int = Field(ge=0)
    reason: str = Field(max_length=500)


class SwapProposal(StrictModel):
    """Drafted swap; applied by the engine only after both members approve."""

    proposal_id: str
    swap_request: SwapRequest
    counterpart_id: MemberId
    counterpart_slot_index: int = Field(ge=0)
    draft_text: str = Field(max_length=1000)
    invariants_ok: bool = True
    status: SwapStatus = SwapStatus.PROPOSED
    agent_decision: AgentDecision | None = None


class ReservePoint(StrictModel):
    """One point of the sweep; reserve_pct is expressed in percent (3 = 3%)."""

    reserve_pct: Decimal = Field(ge=0, le=100)
    completion_probability: float = Field(ge=0.0, le=1.0)
    expected_shortfall: Money


class StressTestResult(StrictModel):
    """Monte Carlo completion odds per reserve percentage; seeded, deterministic.

    reserve_pct is an upfront deposit collected before the first payout.
    """

    n_sims: int = Field(default=10_000, ge=1)
    seed: int = 42
    points: list[ReservePoint] = Field(min_length=1)
    recommended_reserve_pct: Decimal = Field(ge=0, le=Decimal("100"))
    explanation: str | None = None


class AgentChatSession(StrictModel):
    session_id: str


class Persona(StrictModel):
    persona_id: str
    display_name: str
    role: Role
    sandbox_email: str | None = None
    avatar: str | None = Field(default=None, max_length=8)


class DemoLoginRequest(StrictModel):
    persona_id: str


class DemoLoginResponse(StrictModel):
    persona: Persona
    token: str | None = None


class CircleAgentChatRequest(StrictModel):
    session_id: str | None = None
    history: list[ChatMessage] = Field(min_length=1, max_length=40)
    confirmed: bool = False


class CircleAgentChatResponse(StrictModel):
    session_id: str
    turn: CircleAgentTurn


class CreateCircleRequest(StrictModel):
    spec: CircleSpec

    @model_validator(mode="after")
    def _must_be_confirmed(self) -> CreateCircleRequest:
        if not self.spec.confirmed:
            raise ValueError("circle creation requires a confirmed CircleSpec")
        return self


class PayPalReferences(StrictModel):
    product_id: str | None = None
    plan_id: str | None = None
    webhook_id: str | None = None


class CircleCreateResponse(StrictModel):
    circle: CircleDetail
    paypal: PayPalReferences


class JoinCircleRequest(StrictModel):
    member_id: MemberId


class JoinCircleResponse(StrictModel):
    circle_id: CircleId
    member_id: MemberId
    subscription_id: str | None = None
    approval_url: HttpUrl | None = None


class NeedSubmissionRequest(StrictModel):
    member_id: MemberId
    raw_text: str = Field(min_length=1, max_length=1000)
    locale: Locale


class OrderProposeRequest(StrictModel):
    seed: int = 42


class OrderApprovalRequest(StrictModel):
    member_id: MemberId


class StressTestRequest(StrictModel):
    reserve_pcts: list[Decimal] = Field(
        min_length=1,
        max_length=11,
        default_factory=lambda: [Decimal(i) for i in range(0, 11)],
    )
    n_sims: int = Field(default=10_000, ge=1, le=1_000_000)
    seed: int = 42

    @model_validator(mode="after")
    def _pcts_are_percent(self) -> StressTestRequest:
        if any(not (0 <= p <= 100) for p in self.reserve_pcts):
            raise ValueError("reserve_pcts are percentages between 0 and 100")
        return self


class CopilotQuestionRequest(StrictModel):
    member_id: MemberId
    question: str = Field(min_length=1, max_length=500)


class SwapCreateRequest(StrictModel):
    request: SwapRequest


class SwapApprovalRequest(StrictModel):
    approver_id: MemberId


class PayNowRequest(StrictModel):
    member_id: MemberId


class PayNowResponse(StrictModel):
    cycle_index: int
    member_id: MemberId
    order_id: str | None = None
    capture_id: str | None = None
    status: ContributionStatus = ContributionStatus.PENDING


class MediationApproveRequest(StrictModel):
    approving_member_id: MemberId | None = None


class LedgerRow(StrictModel):
    row_id: str
    cycle_index: int = Field(ge=0)
    member_id: MemberId
    member_name: str
    amount: PositiveMoney
    status: ContributionStatus
    source: ContributionSource
    paypal_reference: str | None = None
    created_at: datetime | None = None


class PayoutRow(StrictModel):
    payout_id: str
    cycle_index: int
    recipient_id: MemberId
    batch_id: str
    amount: PositiveMoney
    status: PayoutStatus
    created_at: datetime | None = None


class LedgerResponse(StrictModel):
    circle_id: CircleId
    rows: list[LedgerRow]
    payouts: list[PayoutRow]
    contributions_total: Money
    paid_out_total: Money


class AuditResponse(StrictModel):
    circle_id: CircleId
    decisions: list[AgentDecision]


class ReliabilityRow(StrictModel):
    member_id: MemberId
    display_name: str
    score: ReliabilityScore


class ReliabilityResponse(StrictModel):
    circle_id: CircleId
    rows: list[ReliabilityRow]


class WebhookAcknowledgement(StrictModel):
    received: bool
    duplicate: bool = False


class FastForwardRequest(StrictModel):
    to_cycle_index: int = Field(ge=0)


class ResetDemoResponse(StrictModel):
    reset: bool
    circle_id: CircleId


class HealthResponse(StrictModel):
    status: SanityStatus
    environment: Environment
    paypal: SanityStatus
    llm: SanityStatus
    database: SanityStatus
    version: str


class GuardAction(StrEnum):
    RELEASE_POT_PAYOUT = "RELEASE_POT_PAYOUT"
    SEND_CHASE_INVOICE = "SEND_CHASE_INVOICE"
    SEND_INVOICE_REMINDER = "SEND_INVOICE_REMINDER"
    APPLY_SLOT_SWAP = "APPLY_SLOT_SWAP"


class LeashDecision(StrEnum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


LeashPreset = Literal["HANDS_OFF", "STANDARD", "STRICT"]


class LeashThresholds(StrictModel):
    email_change_window_days: int = Field(default=7, ge=1, le=30)
    reminder_min_gap_hours: int = Field(default=48, ge=12, le=168)
    max_reminders_per_cycle: int = Field(default=3, ge=1, le=10)
    swap_cooldown_hours: int = Field(default=24, ge=0, le=168)
    swap_near_payout_hours: int = Field(default=48, ge=0, le=168)
    high_risk_p_late: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)


class LeashPolicy(StrictModel):
    circle_id: CircleId
    version: int
    preset: LeashPreset = "STANDARD"
    thresholds: LeashThresholds
    no_auto_chase_member_ids: list[MemberId] = Field(default_factory=list)


class GuardResult(StrictModel):
    decision: LeashDecision
    rule_ids: list[str] = Field(max_length=20)
    reason: str = Field(max_length=200)
    decision_id: str


class LeashInboxRecipient(StrictModel):
    member_id: MemberId
    name: str
    payout_email: str | None = None


class LeashInboxItem(StrictModel):
    decision_id: str
    action: GuardAction
    subject_type: Literal["payout", "chase_plan", "swap"]
    subject_id: str
    cycle_index: int | None = None
    recipient: LeashInboxRecipient | None = None
    amount: Money | None = None
    rule_ids: list[str] = Field(max_length=20)
    reason: str
    created_at: datetime


class LeashInbox(StrictModel):
    items: list[LeashInboxItem]


class LeashDecisionRow(StrictModel):
    decision_id: str
    circle_id: CircleId
    policy_version: int
    action: GuardAction
    subject_type: Literal["payout", "chase_plan", "swap"]
    subject_id: str
    decision: LeashDecision
    rule_ids: list[str] = Field(max_length=20)
    amount: Money | None = None
    reason: str
    actor: Literal["leash", "organiser"]
    status: Literal["FINAL", "OPEN", "APPROVED", "REJECTED"]
    created_at: datetime
    resolved_at: datetime | None = None


class LeashResolveRequest(StrictModel):
    note: str | None = Field(default=None, max_length=200)


class LeashResolveResponse(StrictModel):
    decision_id: str
    approves: str
    actor: Literal["organiser"]
    result: dict[str, str] | None = None


class LeashErrorCode(StrEnum):
    ORGANISER_ONLY = "ORGANISER_ONLY"
    LEASH_DECISION_NOT_FOUND = "LEASH_DECISION_NOT_FOUND"
    LEASH_ALREADY_RESOLVED = "LEASH_ALREADY_RESOLVED"
    LEASH_STALE_DECISION = "LEASH_STALE_DECISION"
    LEASH_POLICY_INVALID = "LEASH_POLICY_INVALID"
    LEASH_DISABLED = "LEASH_DISABLED"


class LeashApiError(StrictModel):
    """Envelope for every Leash HTTP error response."""

    error: LeashErrorBody


class LeashErrorBody(StrictModel):
    code: LeashErrorCode
    message: str
    details: dict[str, str] | None = None
    request_id: str | None = None
