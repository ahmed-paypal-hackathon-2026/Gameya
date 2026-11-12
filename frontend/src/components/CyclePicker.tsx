// "Run the cycle" picker: each cycle with its number, due date, recipient and status.
import { formatIsoDate } from "../lib/format";
import type { CycleSnapshot, MemberId } from "../types/contracts";
import { initialOf } from "./CircleHero";
import { Dropdown } from "./Dropdown";

export interface CyclePickerProps {
  cycles: CycleSnapshot[];
  names: Partial<Record<MemberId, string>>;
  value: number;
  onChange: (cycleIndex: number) => void;
}

const statusText = (status: string) => status.replace("_", " ");

export function CyclePicker({ cycles, names, value, onChange }: CyclePickerProps) {
  const index = Math.max(
    0,
    cycles.findIndex((c) => c.cycle_index === value),
  );
  const current = cycles[index];
  if (!current) return null;
  const who = (c: CycleSnapshot) =>
    c.recipient_id ? (names[c.recipient_id] ?? c.recipient_id) : "No recipient yet";
  return (
    <Dropdown
      items={cycles}
      selectedIndex={index}
      onPick={(c) => onChange(c.cycle_index)}
      itemKey={(c) => String(c.cycle_index)}
      triggerLabel={`Cycle ${current.cycle_index + 1}, due ${formatIsoDate(current.due_date)}, pot to ${who(current)}, ${statusText(current.status)}. Change cycle`}
      listLabel="Choose a cycle"
      heading="Cycles"
      renderTrigger={(c) => (
        <>
          <span className="cycle-badge" aria-hidden="true">
            {c.cycle_index + 1}
          </span>
          <span className="picker__who">
            <span className="picker__name">
              Cycle {c.cycle_index + 1} · due {formatIsoDate(c.due_date)}
            </span>
            <span className="picker__sub">
              Pot to <bdi>{who(c)}</bdi>
            </span>
          </span>
          <span className={`tag tag--${c.status}`}>{statusText(c.status)}</span>
        </>
      )}
      renderOption={(c) => (
        <>
          <span className="cycle-badge" aria-hidden="true">
            {c.cycle_index + 1}
          </span>
          <span className="picker__option-text">
            <span className="picker__option-name">{formatIsoDate(c.due_date)}</span>
            <span className="picker__option-sub">
              {c.recipient_id ? (
                <>
                  <span className="avatar avatar--xs" aria-hidden="true">
                    {initialOf(who(c))}
                  </span>
                  <bdi>{who(c)}</bdi>
                </>
              ) : (
                "No recipient yet"
              )}
            </span>
          </span>
          <span className={`tag tag--${c.status}`}>{statusText(c.status)}</span>
        </>
      )}
    />
  );
}
