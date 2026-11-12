// Organiser buttons for one cycle: Collect (send PayPal invoices), Sync payments (read
// invoice status from PayPal), Chase (Smart Chase follows up the late), Close cycle (the
// engine checks the ledger, then pays the pot once). Results are plain text.
import { useState } from "react";
import { type CycleAction, cycleIdFor, runCycleAction } from "../api/client";
import type { CircleDetail, MemberId } from "../types/contracts";
import { CyclePicker } from "./CyclePicker";

const BUTTONS: { action: CycleAction; label: string }[] = [
  { action: "collect", label: "Collect" },
  { action: "sync", label: "Sync payments" },
  { action: "chase", label: "Chase" },
  { action: "close", label: "Close cycle" },
];

export function CycleActions({
  circle,
  names,
  onChanged,
}: {
  circle: CircleDetail;
  names: Partial<Record<MemberId, string>>;
  onChanged?: () => void;
}) {
  const [index, setIndex] = useState(circle.current_cycle_index);
  const [busy, setBusy] = useState<CycleAction | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  async function run(action: CycleAction) {
    setBusy(action);
    setMessage(null);
    try {
      setMessage(await runCycleAction(cycleIdFor(circle.circle_id, index), action));
      setFailed(false);
      onChanged?.();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
      setFailed(true);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card" aria-labelledby="cycle-title">
      <div className="card__head">
        <h2 id="cycle-title">Run the cycle</h2>
        <p className="muted">
          Collect sends the PayPal invoices, Sync reads who paid, Chase follows up late members, and
          Close checks the ledger and pays the pot once.
        </p>
      </div>
      <div className="cycle-pick">
        <CyclePicker cycles={circle.cycles} names={names} value={index} onChange={setIndex} />
      </div>
      <div className="actions">
        {BUTTONS.map((b) => (
          <button
            key={b.action}
            type="button"
            className={b.action === "close" ? "primary" : undefined}
            disabled={busy !== null}
            onClick={() => run(b.action)}
          >
            {busy === b.action ? `${b.label}...` : b.label}
          </button>
        ))}
      </div>
      {message ? (
        <p className={failed ? "error" : "notice"} role={failed ? "alert" : "status"} dir="auto">
          {message}
        </p>
      ) : null}
    </section>
  );
}
