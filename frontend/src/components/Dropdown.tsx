// Shared dropdown: a styled button that opens a listbox. Every picker on the site uses it,
// so they look and behave the same. Keyboard: Enter/Space/arrows open; arrows, Home and End
// move; Enter/Space pick; Escape closes; a press outside closes without changing anything.
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

export interface DropdownProps<T> {
  items: T[];
  selectedIndex: number;
  onPick: (item: T, index: number) => void;
  /** Accessible name of the closed button, e.g. "Viewing as Amina, Organiser. Change member". */
  triggerLabel: string;
  /** Accessible name of the open list, e.g. "Choose a member". */
  listLabel: string;
  heading?: string;
  renderTrigger: (item: T, open: boolean) => ReactNode;
  renderOption: (item: T, selected: boolean) => ReactNode;
  itemKey: (item: T) => string;
  /** Visual size: "pill" for the dark header pill, "field" for form-sized pickers. */
  variant?: "pill" | "field";
  align?: "left" | "right";
}

export function Dropdown<T>({
  items,
  selectedIndex,
  onPick,
  triggerLabel,
  listLabel,
  heading,
  renderTrigger,
  renderOption,
  itemKey,
  variant = "field",
  align = "left",
}: DropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();
  const current = items[selectedIndex] ?? items[0];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.focus();
  }, [open]);

  if (current === undefined) return null;

  function show() {
    setActive(Math.max(0, selectedIndex));
    setOpen(true);
  }

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }

  function pick(index: number) {
    close(true);
    const item = items[index];
    if (item !== undefined && index !== selectedIndex) onPick(item, index);
  }

  function onTriggerKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      show();
    }
  }

  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    const last = items.length - 1;
    if (e.key === "ArrowDown") setActive((i) => (i >= last ? 0 : i + 1));
    else if (e.key === "ArrowUp") setActive((i) => (i <= 0 ? last : i - 1));
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(last);
    else if (e.key === "Enter" || e.key === " ") pick(active);
    else if (e.key === "Escape" || e.key === "Tab") close(e.key === "Escape");
    else return;
    e.preventDefault();
  }

  return (
    <div className={`picker picker--${variant} picker--${align}`} ref={root}>
      <button
        ref={trigger}
        type="button"
        className={open ? "picker__trigger picker__trigger--open" : "picker__trigger"}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={triggerLabel}
        onClick={() => (open ? close(false) : show())}
        onKeyDown={onTriggerKey}
      >
        {renderTrigger(current, open)}
        <svg className="picker__chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M4 6l4 4 4-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open ? (
        <ul
          ref={list}
          id={listId}
          className="picker__list"
          role="listbox"
          tabIndex={-1}
          aria-label={listLabel}
          aria-activedescendant={`${listId}-${active}`}
          onKeyDown={onListKey}
        >
          {heading ? (
            <li className="picker__heading" role="presentation">
              {heading}
            </li>
          ) : null}
          {items.map((item, i) => {
            const selected = i === selectedIndex;
            const cls = [
              "picker__option",
              selected ? "picker__option--selected" : "",
              i === active ? "picker__option--active" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <li
                key={itemKey(item)}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={selected}
                className={cls}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(i)}
              >
                {renderOption(item, selected)}
                <svg className="picker__check" viewBox="0 0 16 16" aria-hidden="true">
                  <path
                    d="M3.5 8.5l3 3 6-7"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
