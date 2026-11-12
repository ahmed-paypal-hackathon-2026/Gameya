// The Circle Agent's proposed circle, shown for the organiser to check. Nothing is sent
// to PayPal until the organiser presses Confirm (the chat itself can never confirm).
import { useState } from "react";
import type { CircleSpec } from "../types/contracts";
import { formatIsoDate, formatUsd } from "../lib/format";

export interface CircleSpecCardProps {
  spec: CircleSpec;
  busy?: boolean;
  onConfirm: (spec: CircleSpec, reservePct: string) => void;
}

export function CircleSpecCard({ spec, busy = false, onConfirm }: CircleSpecCardProps) {
  const [reserve, setReserve] = useState("0");
  const pot = (Number(spec.contribution_amount) * spec.member_count).toFixed(2);
  return (
    <section className="card card--inner" aria-labelledby="spec-title" data-testid="spec-card">
      <h3 id="spec-title" dir="auto">
        {spec.name}
      </h3>
      <dl className="facts">
        <div>
          <dt>Members</dt>
          <dd>{spec.member_count}</dd>
        </div>
        <div>
          <dt>Each pays</dt>
          <dd>
            {formatUsd(spec.contribution_amount)} every {spec.cycle_unit === "WEEK" ? "week" : "month"}
          </dd>
        </div>
        <div>
          <dt>Pot each cycle</dt>
          <dd>{formatUsd(pot)}</dd>
        </div>
        <div>
          <dt>Starts</dt>
          <dd>{formatIsoDate(spec.start_date)}</dd>
        </div>
      </dl>
      <label className="field">
        Safety reserve (%), collected once as a setup fee
        <input
          type="number"
          min={0}
          max={10}
          step={1}
          value={reserve}
          onChange={(e) => setReserve(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={() => onConfirm(spec, reserve || "0")}
      >
        {busy ? "Creating on PayPal..." : "Confirm and create on PayPal"}
      </button>
    </section>
  );
}
