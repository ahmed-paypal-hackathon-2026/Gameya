// Typed API client. Mock mode (the default) serves shared/fixtures with simulated latency;
// VITE_USE_MOCKS=0 calls the backend at VITE_API_BASE_URL.
import circleFixture from "../../../shared/fixtures/json/circle-get.json";
import orderFixture from "../../../shared/fixtures/json/circle-order-propose.json";
import reliabilityFixture from "../../../shared/fixtures/json/circle-reliability.json";
import stressFixture from "../../../shared/fixtures/json/circles-stress-test.json";
import auditFixture from "../../../shared/fixtures/json/circle-audit.json";
import ledgerFixture from "../../../shared/fixtures/json/circle-ledger.json";
import chatFixture from "../../../shared/fixtures/json/agent-circle-chat.json";
import createFixture from "../../../shared/fixtures/json/circles-create.json";
import type {
  AuditResponse,
  ChatMessage,
  CircleAgentChatResponse,
  CircleCreateResponse,
  CircleDetail,
  CircleId,
  CircleSpec,
  DemoLoginResponse,
  LedgerResponse,
  Locale,
  MemberId,
  NeedExtraction,
  OrderProposal,
  ReliabilityResponse,
  Role,
  StressTestRequest,
  StressTestResult,
  Viewer,
} from "../types/contracts";
import { apiBase, parseUseMocks } from "./config";
import { MOCK_NEEDS } from "./mockNeeds";

export const USE_MOCKS = parseUseMocks(import.meta.env.VITE_USE_MOCKS);
export const DATA_MODE: "mock" | "live" = USE_MOCKS ? "mock" : "live";
export const CIRCLE_ID: CircleId = import.meta.env.VITE_CIRCLE_ID?.trim() || "circle-golden-01";
const BASE = apiBase(import.meta.env.VITE_API_BASE_URL);

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class OrganiserOnlyError extends Error {
  constructor() {
    super("Reliability data is only available to the circle organiser.");
    this.name = "OrganiserOnlyError";
  }
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function mock<T>(data: unknown): Promise<T> {
  await delay(300);
  // A fresh copy per call, so no screen can mutate another screen's data.
  return structuredClone(data) as T;
}

// The demo-login token lives in memory only (never localStorage): a reload logs in again.
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

/** The backend's error text (FastAPI `detail` or our `error.message`), if it sent one. */
async function errorDetail(r: Response): Promise<string | null> {
  try {
    const body: unknown = await r.json();
    if (body && typeof body === "object") {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
      const error = (body as { error?: { message?: unknown } }).error;
      if (error && typeof error.message === "string") return error.message;
    }
  } catch {
    /* not JSON */
  }
  return null;
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await errorDetail(r);
    throw new ApiError(r.status, detail ?? `${method} ${path} failed with status ${r.status}.`);
  }
  return (await r.json()) as T;
}

/** mem-amina -> persona-amina (the demo personas behind the members). */
export const personaFor = (memberId: MemberId): string =>
  `persona-${memberId.replace(/^mem-/, "")}`;

/** Log in as a demo persona; every later request carries its token. */
export async function loginAs(memberId: MemberId): Promise<void> {
  if (USE_MOCKS) return;
  const res = await request<DemoLoginResponse>("POST", "/auth/demo-login", {
    persona_id: personaFor(memberId),
  });
  setAuthToken(res.token);
}

const circlePath = (circleId: CircleId) => `/circles/${encodeURIComponent(circleId)}`;

/** Only what the screens need to label people. Never carries reliability fields. */
export interface RosterMember {
  member_id: MemberId;
  display_name: string;
  role: Role;
}

export async function getRoster(circleId: CircleId): Promise<RosterMember[]> {
  const circle = USE_MOCKS
    ? await mock<CircleDetail>(circleFixture)
    : await request<CircleDetail>("GET", circlePath(circleId));
  // CircleDetail.members includes p_late; strip it here so it never reaches a component.
  return circle.members.map(({ member_id, display_name, role }) => ({
    member_id,
    display_name,
    role,
  }));
}

/** Organiser-only (plan RSK-09). Member screens never call this. */
export async function getReliability(viewer: Viewer): Promise<ReliabilityResponse> {
  if (viewer.role !== "organiser") throw new OrganiserOnlyError();
  return USE_MOCKS
    ? mock<ReliabilityResponse>(reliabilityFixture)
    : request<ReliabilityResponse>("GET", `${circlePath(viewer.circle_id)}/reliability`);
}

/** Every reserve level from 0% to 10% (the contract allows at most 11). */
export const DEFAULT_STRESS_TEST: StressTestRequest = {
  reserve_pcts: Array.from({ length: 11 }, (_, i) => String(i)),
  n_sims: 10_000,
  seed: 42,
};

export async function runStressTest(
  circleId: CircleId,
  body: StressTestRequest = DEFAULT_STRESS_TEST,
): Promise<StressTestResult> {
  return USE_MOCKS
    ? mock<StressTestResult>(stressFixture)
    : request<StressTestResult>("POST", `${circlePath(circleId)}/stress-test`, body);
}

export async function getOrderProposal(circleId: CircleId): Promise<OrderProposal> {
  // Empty body on purpose: the seed must come from the backend's derive_seed, never from
  // the browser, or tied orders could be steered by picking a seed (P3 hand-off).
  return USE_MOCKS
    ? mock<OrderProposal>(orderFixture)
    : request<OrderProposal>("POST", `${circlePath(circleId)}/order/propose`, {});
}

/** Each member's extracted need, keyed by member_id. */
export async function getCircleNeeds(
  circleId: CircleId,
): Promise<Partial<Record<MemberId, NeedExtraction>>> {
  const list = USE_MOCKS
    ? await mock<NeedExtraction[]>(MOCK_NEEDS)
    : await request<NeedExtraction[]>("GET", `${circlePath(circleId)}/needs`);
  const byMember: Partial<Record<MemberId, NeedExtraction>> = {};
  for (const need of list) {
    if (need.member_id) byMember[need.member_id] = need;
  }
  return byMember;
}

// ---------- live circle flow (plan Task 10) ----------

export async function getCircle(circleId: CircleId): Promise<CircleDetail> {
  return USE_MOCKS
    ? mock<CircleDetail>(circleFixture)
    : request<CircleDetail>("GET", circlePath(circleId));
}

/** Cycle ids follow the engine's convention: `<circle_id>-c<cycle_index>`. */
export const cycleIdFor = (circleId: CircleId, cycleIndex: number): string =>
  `${circleId}-c${cycleIndex}`;

/** One chat turn. The chat only fills the card; it can never create the circle. */
export async function chatWithCircleAgent(
  history: ChatMessage[],
  sessionId: string | null,
): Promise<CircleAgentChatResponse> {
  return USE_MOCKS
    ? mock<CircleAgentChatResponse>(chatFixture)
    : request<CircleAgentChatResponse>("POST", "/agent/circle/chat", {
        session_id: sessionId,
        history,
        confirmed: false,
      });
}

/** Called only after the organiser presses Confirm on the card. */
export async function createCircle(
  spec: CircleSpec,
  reservePct = "0",
): Promise<CircleCreateResponse> {
  return USE_MOCKS
    ? mock<CircleCreateResponse>(createFixture)
    : request<CircleCreateResponse>("POST", "/circles", {
        spec: { ...spec, confirmed: true },
        reserve_pct: reservePct,
      });
}

export async function getLedger(viewer: Viewer): Promise<LedgerResponse> {
  if (viewer.role !== "organiser") throw new OrganiserOnlyError();
  return USE_MOCKS
    ? mock<LedgerResponse>(ledgerFixture)
    : request<LedgerResponse>("GET", `${circlePath(viewer.circle_id)}/ledger`);
}

export async function getAudit(viewer: Viewer): Promise<AuditResponse> {
  if (viewer.role !== "organiser") throw new OrganiserOnlyError();
  return USE_MOCKS
    ? mock<AuditResponse>(auditFixture)
    : request<AuditResponse>("GET", `${circlePath(viewer.circle_id)}/audit`);
}

export type CycleAction = "collect" | "sync" | "chase" | "close";

interface CollectResult {
  invoices: { paypal_invoice_id: string | null }[];
}
interface SyncResult {
  newly_paid: number;
  unpaid_member_ids: string[];
  cycle_status: string;
}
interface ChaseResult {
  actions: { member_id: string; action: string; error: string | null }[];
}
interface CloseResult {
  amount: string;
  status: string;
  paypal_batch_id: string | null;
}

/** Organiser cycle actions. Returns a one-line summary for the screen. */
export async function runCycleAction(cycleId: string, action: CycleAction): Promise<string> {
  if (USE_MOCKS) {
    await delay(300);
    return `Mock mode: "${action}" needs the live API (VITE_USE_MOCKS=0).`;
  }
  const path = `/cycles/${encodeURIComponent(cycleId)}/${action}`;
  if (action === "collect") {
    const r = await request<CollectResult>("POST", path);
    const sent = r.invoices.filter((i) => i.paypal_invoice_id).length;
    return `${sent} PayPal invoices are out for this cycle.`;
  }
  if (action === "sync") {
    const r = await request<SyncResult>("POST", path);
    const unpaid = r.unpaid_member_ids.length;
    const owed = unpaid === 0 ? "Everyone has paid." : `${unpaid} still to pay.`;
    return `${r.newly_paid} newly paid. ${owed} Cycle: ${r.cycle_status}.`;
  }
  if (action === "chase") {
    const r = await request<ChaseResult>("POST", path);
    if (r.actions.length === 0) return "Nobody owes anything: no chase needed.";
    return r.actions
      .map((a) => `${a.member_id}: ${a.action}${a.error ? ` (failed at ${a.error})` : ""}`)
      .join("; ");
  }
  const r = await request<CloseResult>("POST", path);
  return r.status === "BLOCKED"
    ? `The $${r.amount} pot is ready, but Payouts is not enabled on this PayPal app: nothing was sent.`
    : `Payout of $${r.amount} sent. PayPal batch ${r.paypal_batch_id ?? "pending"} (${r.status}).`;
}

export interface MyInvoice {
  cycle_index: number;
  paypal_invoice_id: string;
  amount: string;
  status: string;
  paid: boolean;
  pay_url: string;
}

/** The viewer's own PayPal invoices, each with its PayPal payment page. */
export async function getMyInvoices(circleId: CircleId): Promise<MyInvoice[]> {
  return USE_MOCKS
    ? mock<MyInvoice[]>([])
    : request<MyInvoice[]>("GET", `${circlePath(circleId)}/my-invoices`);
}

export async function submitNeed(
  circleId: CircleId,
  memberId: MemberId,
  rawText: string,
  locale: Locale,
): Promise<NeedExtraction> {
  if (USE_MOCKS) {
    await delay(300);
    throw new ApiError(0, "Mock mode: submitting a need needs the live API.");
  }
  return request<NeedExtraction>("POST", `${circlePath(circleId)}/needs`, {
    member_id: memberId,
    raw_text: rawText,
    locale,
  });
}

export async function approveOrder(circleId: CircleId, memberId: MemberId): Promise<OrderProposal> {
  if (USE_MOCKS) {
    await delay(300);
    throw new ApiError(0, "Mock mode: approving needs the live API.");
  }
  return request<OrderProposal>("POST", `${circlePath(circleId)}/order/approve`, {
    member_id: memberId,
  });
}
