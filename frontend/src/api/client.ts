// Typed API client. Mock mode (the default) serves shared/fixtures with simulated latency;
// VITE_USE_MOCKS=0 calls the backend at VITE_API_BASE_URL.
import circleFixture from "../../../shared/fixtures/json/circle-get.json";
import orderFixture from "../../../shared/fixtures/json/circle-order-propose.json";
import reliabilityFixture from "../../../shared/fixtures/json/circle-reliability.json";
import stressFixture from "../../../shared/fixtures/json/circles-stress-test.json";
import type {
  CircleDetail,
  CircleId,
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

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(r.status, `${method} ${path} failed with status ${r.status}.`);
  return (await r.json()) as T;
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
