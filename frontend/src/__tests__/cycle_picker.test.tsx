import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CyclePicker } from "../components/CyclePicker";
import type { CycleSnapshot } from "../types/contracts";

afterEach(cleanup);

const cycle = (i: number, status: CycleSnapshot["status"], recipient: string | null): CycleSnapshot => ({
  cycle_index: i,
  status,
  due_date: `2026-${String(11 + i).padStart(2, "0")}-01`,
  amount_due: "100.00",
  pot_total: "600.00",
  recipient_id: recipient,
  unpaid_members: [],
});

const CYCLES = [cycle(0, "paid_out", "mem-youssef"), cycle(1, "due", "mem-sara")];
const NAMES = { "mem-youssef": "Youssef", "mem-sara": "Sara" };

describe("CyclePicker", () => {
  it("shows the chosen cycle, its due date, recipient and status", () => {
    render(<CyclePicker cycles={CYCLES} names={NAMES} value={1} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: /cycle 2/i });
    expect(trigger.textContent).toContain("Cycle 2");
    expect(trigger.textContent).toContain("1 Dec 2026");
    expect(trigger.textContent).toContain("Sara");
    expect(trigger.textContent).toContain("due");
  });

  it("lists every cycle and picks one by click", () => {
    const onChange = vi.fn();
    render(<CyclePicker cycles={CYCLES} names={NAMES} value={1} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /cycle 2/i }));
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0].textContent).toContain("Youssef");
    expect(options[0].textContent).toContain("paid out");
    fireEvent.click(options[0]);
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it("picks with the keyboard", () => {
    const onChange = vi.fn();
    render(<CyclePicker cycles={CYCLES} names={NAMES} value={1} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /cycle 2/i }), { key: "Enter" });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowUp" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(0);
  });
});
