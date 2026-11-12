import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import reliabilityFixture from "../../../shared/fixtures/json/circle-reliability.json";
import App from "../App";
import orderCardSource from "../components/OrderApprovalCard.tsx?raw";
import memberHomeSource from "../pages/member/MemberHome.tsx?raw";
import { getReliability, OrganiserOnlyError } from "../api/client";
import { OrderApprovalCard } from "../components/OrderApprovalCard";
import { ReliabilityGrid, topFactors } from "../grids/ReliabilityGrid";
import { StressTestGrid } from "../grids/StressTestGrid";
import type { OrderProposal, ReliabilityRow, StressTestResult } from "../types/contracts";

afterEach(cleanup);

const HOSTILE = '<img src=x onerror="window.__pwned=1">';

const reliabilityRows: ReliabilityRow[] = [
  {
    member_id: "mem-a",
    display_name: "<b>Mallory</b>",
    score: {
      member_id: "mem-a",
      p_late: 0.63,
      top_factors: [
        { feature: "f1", contribution: 0.1, label: "Small raise" },
        { feature: "f2", contribution: -0.9, label: "Never paid late" },
        { feature: "f3", contribution: 0.5, label: HOSTILE },
        { feature: "f4", contribution: 0.3, label: "Fourth factor" },
      ],
    },
  },
  {
    member_id: "mem-b",
    display_name: "Sara",
    score: {
      member_id: "mem-b",
      p_late: 0.02,
      top_factors: [{ feature: "payment_history", contribution: 0, label: "No payment history yet" }],
    },
  },
];

describe("ReliabilityGrid", () => {
  it("keeps the 3 largest factors by absolute impact", () => {
    const labels = topFactors(reliabilityRows[0].score.top_factors).map((f) => f.label);
    expect(labels).toEqual(["Never paid late", HOSTILE, "Fourth factor"]);
  });

  it("colours positive red, negative green, and renders text without HTML", async () => {
    const { container } = render(<ReliabilityGrid rows={reliabilityRows} />);
    await screen.findByText("Never paid late");

    const lowers = container.querySelectorAll(".factor--lowers");
    const raises = container.querySelectorAll(".factor--raises");
    expect(lowers).toHaveLength(1);
    expect(lowers[0].textContent).toContain("\u22120.90");
    expect(lowers[0].textContent).toContain("lowers risk");
    expect(raises).toHaveLength(2);
    expect(container.querySelector(".factor--neutral")?.textContent).toContain(
      "No payment history yet",
    );
    expect(screen.queryByText("Small raise")).toBeNull();

    // Markup in labels and names is shown literally, never parsed.
    expect(container.querySelector(".ag-root img, .ag-root b")).toBeNull();
    expect(screen.getByText(HOSTILE)).toBeTruthy();
    expect(screen.getByText("<b>Mallory</b>")).toBeTruthy();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();

    expect(screen.getByText("63%")).toBeTruthy();
    expect(screen.getByText("2%")).toBeTruthy();
  });
});

describe("StressTestGrid", () => {
  const result: StressTestResult = {
    n_sims: 10000,
    seed: 42,
    points: [
      { reserve_pct: "10", completion_probability: 0.98, expected_shortfall: "12.00" },
      { reserve_pct: "0", completion_probability: 0.82, expected_shortfall: "108.00" },
      { reserve_pct: "3", completion_probability: 0.97, expected_shortfall: "36.00" },
    ],
    recommended_reserve_pct: "3.00",
    explanation: "A 3% reserve <script>bad()</script> raises completion.",
  };

  it("shows the explanation above the grid and highlights the recommended reserve", async () => {
    const { container } = render(<StressTestGrid result={result} />);
    await screen.findByText("Recommended");

    const explanation = screen.getByTestId("stress-explanation");
    const grid = container.querySelector(".ag-theme-quartz")!;
    expect(explanation.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(explanation.textContent).toContain("<script>bad()</script>");
    expect(container.querySelector("script")).toBeNull();

    const highlighted = container.querySelectorAll(".ag-center-cols-container .row--recommended");
    expect(highlighted).toHaveLength(1);
    const cells = within(highlighted[0] as HTMLElement);
    expect(cells.getByText("3%")).toBeTruthy();
    expect(cells.getByText("97%")).toBeTruthy();
    expect(cells.getByText("$36.00")).toBeTruthy();

    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByText("$108.00")).toBeTruthy();
    expect(screen.getByText(/Based on 10,000 simulated circles/)).toBeTruthy();
  });
});

describe("OrderApprovalCard", () => {
  const proposal: OrderProposal = {
    seed: 1,
    assignments: [
      { member_id: "mem-b", slot_index: 1, payout_date: "2026-12-01", cost: 0.2 },
      { member_id: "mem-a", slot_index: 0, payout_date: "2026-11-01", cost: 0 },
    ],
    explanations: { "mem-b": "**Omar** gets <a href='x'>December</a>." },
  };

  it("shows each member's extracted need and plain-text explanations", () => {
    const { container } = render(
      <OrderApprovalCard
        proposal={proposal}
        names={{ "mem-a": "Amina", "mem-b": "Omar" }}
        needs={{
          "mem-b": {
            member_id: "mem-b",
            category: "EDUCATION",
            target_date: "2026-12-15",
            amount_needed: null,
            urgency: 5,
            flexibility: "low",
            confidence: 0.9,
            raw_month_hint: null,
          },
        }}
        viewerId="mem-b"
      />,
    );
    expect(screen.getByTestId("need-mem-b").textContent).toBe("Education, needed by 15 Dec 2026");
    expect(screen.getByTestId("need-mem-a").textContent).toBe("No need submitted");
    expect(screen.getByText("**Omar** gets <a href='x'>December</a>.")).toBeTruthy();
    expect(container.querySelector("a, strong")).toBeNull();

    const items = container.querySelectorAll(".order-item");
    expect(items[0].textContent).toContain("Slot 1 of 2");
    expect(items[0].textContent).toContain("Amina");
    expect(items[1].textContent).toContain("You");
    expect(items[1].textContent).toContain("0.20");
  });

  it("does not claim 'no need' when needs failed to load", () => {
    render(
      <OrderApprovalCard proposal={proposal} names={{}} needs={{}} needsUnavailable />,
    );
    expect(screen.getByTestId("need-mem-a").textContent).toBe("Need not available");
    expect(screen.getByRole("status").textContent).toContain("could not be loaded");
  });
});

describe("reliability privacy", () => {
  const reliabilityLabels = reliabilityFixture.rows.flatMap((r) =>
    r.score.top_factors.map((f) => f.label),
  );

  it("refuses to load reliability data for a member", async () => {
    await expect(
      getReliability({ viewer_id: "mem-omar", role: "member", circle_id: "circle-golden-01" }),
    ).rejects.toBeInstanceOf(OrganiserOnlyError);
  });

  it("shows reliability to the organiser and hides it after switching to a member", async () => {
    const { container } = render(<App />);
    await screen.findByText("Chance of paying late", {}, { timeout: 3000 });
    expect((await screen.findAllByText(reliabilityLabels[0], {}, { timeout: 3000 })).length).toBeGreaterThan(0);
    await screen.findByText("Recommended", {}, { timeout: 3000 });
    await screen.findByText(/needed by 15 Dec 2026/, {}, { timeout: 3000 });

    fireEvent.click(screen.getByRole("button", { name: /viewing as/i }));
    fireEvent.click(
      within(screen.getByRole("listbox", { name: "Choose a member" })).getByRole("option", {
        name: /Omar/,
      }),
    );
    await screen.findByText(/needed by 15 Dec 2026/, {}, { timeout: 3000 });

    await waitFor(() => expect(screen.queryByText("Chance of paying late")).toBeNull());
    const text = container.textContent ?? "";
    expect(text).not.toContain("Payment reliability");
    expect(text).not.toContain("stress test");
    for (const label of reliabilityLabels) expect(text).not.toContain(label);
    expect(text).not.toMatch(/\d+%/);
  });

  it("member-facing modules never import reliability code", () => {
    for (const src of [memberHomeSource, orderCardSource]) {
      expect(src.length).toBeGreaterThan(0);
      expect(src).not.toMatch(/getReliability|ReliabilityGrid|p_late|top_factors|StressTest/);
    }
  });
});
