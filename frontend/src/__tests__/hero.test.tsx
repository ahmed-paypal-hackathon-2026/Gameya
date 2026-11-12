import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { type CircleOverview, getCircleOverview } from "../api/client";
import { CircleHero, initialOf, scheduleOf } from "../components/CircleHero";

describe("circle overview (member-safe)", () => {
  it("strips every risk field before it reaches a component", async () => {
    const o = await getCircleOverview("circle-golden-01");
    const text = JSON.stringify(o);
    expect(text).not.toMatch(/p_late|days_late|reserve_pct|contribution_status|unpaid_members/);
    expect(o.members.length).toBe(6);
    expect(Object.keys(o.members[0]).sort()).toEqual(["display_name", "member_id", "role", "slot_index"]);
  });

  it("schedules every member slot, dating future slots from the start date", async () => {
    const o = await getCircleOverview("circle-golden-01");
    const s = scheduleOf(o);
    expect(s.map((c) => c.cycle_index)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(s[3].due_date).toBe("2027-02-01"); // existing engine cycle kept as is
    expect(s[4]).toMatchObject({ due_date: "2027-03-01", status: "upcoming" });
    expect(s[5].due_date).toBe("2027-04-01");
    // every member appears exactly once, even when engine cycles and slots disagree
    const ids = s.map((c) => c.recipient_id);
    expect(new Set(ids).size).toBe(6);
    expect(ids).not.toContain(null);
  });

  it("handles weekly circles and missing slots", () => {
    const o: CircleOverview = {
      circle_id: "c",
      name: "Weekly",
      member_count: 3,
      contribution_amount: "10.00",
      cycle_unit: "WEEK",
      start_date: "2026-12-29",
      current_cycle_index: 0,
      members: [{ member_id: "m1", display_name: "Mona", role: "organiser", slot_index: 0 }],
      cycles: [],
    };
    const s = scheduleOf(o);
    expect(s.map((c) => c.due_date)).toEqual(["2026-12-29", "2027-01-05", "2027-01-12"]);
    expect(s[0].recipient_id).toBe("m1");
    expect(s[1].recipient_id).toBeNull();
  });
});

describe("initialOf", () => {
  it("takes the first visible character, including Arabic and emoji", () => {
    expect(initialOf("  sara ")).toBe("S");
    expect(initialOf("سارة")).toBe("س");
    expect(initialOf("😀 Joy")).toBe("😀");
    expect(initialOf("")).toBe("?");
  });
});

describe("CircleHero for a member", () => {
  it("shows the pot and the member's own turn, never a percentage", async () => {
    const { container } = render(<CircleHero circleId="circle-golden-01" viewerId="mem-omar" />);
    await screen.findByText("Golden Circle");
    expect(screen.getByText("You received the pot")).toBeTruthy();
    expect(screen.getByText("$600.00")).toBeTruthy();
    expect(container.querySelectorAll(".timeline__item")).toHaveLength(6);
    expect(container.querySelectorAll(".ring__dot")).toHaveLength(6);
    expect(container.textContent).not.toMatch(/\d+%/);
    expect(container.querySelector("script, img")).toBeNull();
  });
});
