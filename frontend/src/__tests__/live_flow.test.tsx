// Live-mode flows (plan Task 10): the client runs against a mocked fetch, so these
// check what the browser would send to the real API.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AgentDecision,
  CircleAgentChatResponse,
  CircleCreateResponse,
  LedgerResponse,
  Viewer,
} from "../types/contracts";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

let calls: Call[] = [];
let routes: Record<string, unknown> = {};

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = init?.method ?? "GET";
  calls.push({
    url,
    method,
    headers: (init?.headers ?? {}) as Record<string, string>,
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
  });
  const key = Object.keys(routes).find((k) => {
    const [m, path] = k.split(" ");
    return m === method && url.endsWith(path);
  });
  if (!key) return Promise.resolve(new Response("{}", { status: 404 }));
  return Promise.resolve(
    new Response(JSON.stringify(routes[key]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

async function live() {
  vi.stubEnv("VITE_USE_MOCKS", "0");
  vi.stubEnv("VITE_API_BASE_URL", "http://api.test");
  vi.resetModules();
  return import("../api/client");
}

beforeEach(() => {
  calls = [];
  routes = {
    "POST /api/v1/auth/demo-login": {
      persona: { persona_id: "persona-amina", display_name: "Amina", role: "organiser" },
      token: "persona-amina.sig",
    },
  };
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const ORGANISER: Viewer = { viewer_id: "mem-amina", role: "organiser", circle_id: "circle-golden-01" };

const LEDGER: LedgerResponse = {
  circle_id: "circle-golden-01",
  rows: [
    {
      row_id: "ct-1",
      cycle_index: 0,
      member_id: "mem-sara",
      member_name: "Sara",
      amount: "100.00",
      status: "paid",
      source: "invoice",
      paypal_reference: "INV2-ABCD-1234",
      created_at: null,
    },
  ],
  payouts: [
    {
      payout_id: "po-1",
      cycle_index: 0,
      recipient_id: "mem-amina",
      batch_id: "PB-REAL-77",
      amount: "600.00",
      status: "paid_out",
      created_at: null,
    },
  ],
  contributions_total: "100.00",
  paid_out_total: "600.00",
};

describe("live client", () => {
  it("sends the bearer token on every request", async () => {
    const client = await live();
    routes["GET /api/v1/circles/circle-golden-01/ledger"] = LEDGER;
    routes["GET /api/v1/circles/circle-golden-01/audit"] = { circle_id: "x", decisions: [] };
    await client.loginAs("mem-amina");
    expect(calls[0].body).toEqual({ persona_id: "persona-amina" });
    await client.getLedger(ORGANISER);
    await client.getAudit(ORGANISER);
    const authed = calls.slice(1);
    expect(authed).toHaveLength(2);
    for (const c of authed) expect(c.headers.Authorization).toBe("Bearer persona-amina.sig");
  });

  it("shows the backend's error message", async () => {
    const client = await live();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "the pot is incomplete: 500.00 of 600.00 USD" }), {
            status: 409,
          }),
        ),
      ),
    );
    await expect(client.runCycleAction("circle-golden-01-c0", "close")).rejects.toThrow(
      "the pot is incomplete",
    );
  });
});

describe("Circle Agent chat", () => {
  it("shows a confirm card and only calls POST /circles after Confirm", async () => {
    await live();
    const { Chat } = await import("../pages/organiser/Chat");
    const turn: CircleAgentChatResponse = {
      session_id: "chat-1",
      turn: {
        reply: "Here is your circle. Confirm to create it on PayPal.",
        spec: {
          name: "Friends Circle",
          member_count: 6,
          contribution_amount: "100.00",
          currency: "USD",
          cycle_unit: "MONTH",
          start_date: "2026-12-01",
          organiser_id: "mem-amina",
          purpose: null,
          confirmed: false,
        },
        tool_calls: [],
        agent_decision: null,
        needs_confirmation: true,
      },
    };
    const created = {
      circle: {},
      paypal: { product_id: "PROD-9XY", plan_id: "P-5PLAN", webhook_id: null },
    } as unknown as CircleCreateResponse;
    routes["POST /api/v1/agent/circle/chat"] = turn;
    routes["POST /api/v1/circles"] = created;

    render(<Chat />);
    fireEvent.change(screen.getByLabelText("Message to the Circle Agent"), {
      target: { value: "6 friends, $100 a month, starting 1 December" },
    });
    fireEvent.click(screen.getByText("Send"));
    await screen.findByTestId("spec-card");
    expect(screen.getByText("$600.00")).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/api/v1/circles"))).toBe(false);
    expect(calls[0].body).toMatchObject({ confirmed: false });

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "3" } });
    fireEvent.click(screen.getByText("Confirm and create on PayPal"));
    await screen.findByTestId("created");
    const create = calls.find((c) => c.url.endsWith("/api/v1/circles"));
    expect(create?.method).toBe("POST");
    expect(create?.body).toMatchObject({ spec: { confirmed: true }, reserve_pct: "3" });
    expect(screen.getByText("PROD-9XY")).toBeTruthy();
    expect(screen.getByText("P-5PLAN")).toBeTruthy();
  });
});

describe("LedgerGrid", () => {
  it("shows PayPal ids and a pinned totals row", async () => {
    const { LedgerGrid } = await import("../grids/LedgerGrid");
    const { container } = render(<LedgerGrid data={LEDGER} names={{ "mem-amina": "Amina" }} />);
    await screen.findByText("INV2-ABCD-1234");
    const pinned = container.querySelector(".ag-floating-bottom");
    expect(pinned?.textContent).toContain("Total");
    expect(pinned?.textContent).toContain("$100.00");
    expect(pinned?.textContent).toContain("Paid out $600.00");
    expect(screen.getByText("PB-REAL-77")).toBeTruthy();
    expect(container.textContent).toContain("to Amina");
  });
});

describe("AuditGrid", () => {
  it("opens a detail row with the rationale and PayPal calls, as plain text", async () => {
    const { AuditGrid } = await import("../grids/AuditGrid");
    const decisions: AgentDecision[] = [
      {
        decision_id: "agd-1",
        agent: "smart_chase",
        circle_id: "circle-golden-01",
        cycle_index: 0,
        model: "template",
        tool_calls: [
          {
            name: "send_invoice_reminder",
            arguments: { invoice_id: "INV2-OMAR" },
            result_summary: null,
          },
        ],
        rationale: "REMINDER chosen. <img src=x onerror=alert(1)>",
        created_at: "2026-11-03T12:00:00Z",
        human_approved: true,
      },
    ];
    const { container } = render(<AuditGrid decisions={decisions} />);
    const cell = await screen.findByText("Smart Chase");
    expect(screen.queryByTestId("audit-detail")).toBeNull();
    fireEvent.click(cell);
    const detail = await screen.findByTestId("audit-detail");
    expect(detail.textContent).toContain("send_invoice_reminder");
    expect(detail.textContent).toContain("invoice_id: INV2-OMAR");
    expect(detail.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("member view", () => {
  it("never requests reliability, ledger, audit or the stress test", async () => {
    await live();
    routes["GET /api/v1/circles/circle-golden-01/my-invoices"] = [
      {
        cycle_index: 0,
        paypal_invoice_id: "INV2-OMAR-1",
        amount: "100.00",
        status: "SENT",
        paid: false,
        pay_url: "https://www.sandbox.paypal.com/invoice/p/#INV2-OMAR-1",
      },
    ];
    routes["POST /api/v1/circles/circle-golden-01/order/propose"] = {
      seed: 1,
      assignments: [
        { member_id: "mem-omar", slot_index: 0, payout_date: "2026-11-01", cost: 0 },
      ],
      explanations: {},
    };
    routes["GET /api/v1/circles/circle-golden-01/needs"] = [];
    const { MemberHome } = await import("../pages/member/MemberHome");
    render(
      <MemberHome
        viewer={{ viewer_id: "mem-omar", role: "member", circle_id: "circle-golden-01" }}
        names={{ "mem-omar": "Omar" }}
      />,
    );
    const pay = await screen.findByText("Pay with PayPal");
    expect(pay.getAttribute("href")).toBe("https://www.sandbox.paypal.com/invoice/p/#INV2-OMAR-1");
    expect(pay.getAttribute("rel")).toContain("noopener");
    await screen.findByText("Approve this order");
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(3));
    for (const c of calls) expect(c.url).not.toMatch(/reliability|ledger|audit|stress-test/);
  });
});
