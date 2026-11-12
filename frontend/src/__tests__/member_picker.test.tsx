import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberPicker } from "../components/MemberPicker";
import type { RosterMember } from "../api/client";

afterEach(cleanup);

const ROSTER: RosterMember[] = [
  { member_id: "mem-amina", display_name: "Amina", role: "organiser" },
  { member_id: "mem-omar", display_name: "Omar", role: "member" },
  { member_id: "mem-sara", display_name: "<b>Sara</b>", role: "member" },
];

function setup(value = "mem-amina") {
  const onChange = vi.fn();
  render(<MemberPicker members={ROSTER} value={value} onChange={onChange} />);
  return { onChange, trigger: screen.getByRole("button", { name: /viewing as/i }) };
}

describe("MemberPicker", () => {
  it("shows the current member and their role on the closed button", () => {
    const { trigger } = setup();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toContain("Amina");
    expect(trigger.textContent).toContain("Organiser");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens a list of every member and switches on click", () => {
    const { trigger, onChange } = setup();
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[0].getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("option", { name: /Omar/ }));
    expect(onChange).toHaveBeenCalledWith("mem-omar");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("works with the keyboard: arrows move, Enter picks, Escape closes", () => {
    const { trigger, onChange } = setup();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("mem-omar");

    fireEvent.keyDown(trigger, { key: "Enter" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("closes when clicking outside, without changing the member", () => {
    const { trigger, onChange } = setup();
    fireEvent.click(trigger);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows member names as plain text", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);
    expect(screen.getByText("<b>Sara</b>")).toBeTruthy();
    expect(document.querySelector(".picker b")).toBeNull();
  });
});
