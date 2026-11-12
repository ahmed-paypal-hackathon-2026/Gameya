// The circle at a glance, for every member: the pot, whose turn it is, your turn, and the
// payout timeline. Built only from getCircleOverview, which carries no risk data, so it is
// safe on member screens. Names are member-chosen text and are always rendered as text.
import { type CircleOverview, getCircleOverview } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import { formatIsoDate, formatUsd } from "../lib/format";
import type { MemberId } from "../types/contracts";

type Cycle = CircleOverview["cycles"][number];
type Stage = "done" | "now" | "next" | "issue";

function stageOf(c: Cycle, current: number): Stage {
  if (c.status === "paid_out") return "done";
  if (c.status === "failed") return "issue";
  return c.cycle_index === current ? "now" : "next";
}

const STAGE_TEXT: Record<Stage, string> = {
  done: "Received",
  now: "This cycle",
  next: "Upcoming",
  issue: "Needs attention",
};

/** First visible character of a name, for the avatar. Plain text only. */
export function initialOf(name: string): string {
  const first = Array.from(name.trim())[0];
  return first ? first.toLocaleUpperCase() : "?";
}

/** ISO date `iso` moved forward by `n` months or weeks, as an ISO date (UTC, no time zone drift). */
function addPeriods(iso: string, n: number, unit: "MONTH" | "WEEK"): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (unit === "WEEK") d.setUTCDate(d.getUTCDate() + 7 * n);
  else d.setUTCMonth(d.getUTCMonth() + n);
  return d.toISOString().slice(0, 10);
}

/**
 * One entry per payout slot. Cycles that already exist come from the engine; later slots
 * use the member's agreed slot and the circle's schedule, so every member appears.
 */
export function scheduleOf(o: CircleOverview): Cycle[] {
  const total = Math.max(o.member_count, o.cycles.length);
  const out: Cycle[] = [];
  // Members who already have an engine cycle never appear twice; the rest follow slot order.
  const taken = new Set(o.cycles.map((c) => c.recipient_id).filter((id): id is MemberId => !!id));
  const waiting = o.members
    .filter((m) => !taken.has(m.member_id))
    .sort((a, b) => (a.slot_index ?? Number.MAX_SAFE_INTEGER) - (b.slot_index ?? Number.MAX_SAFE_INTEGER));
  for (let i = 0; i < total; i++) {
    const existing = o.cycles.find((c) => c.cycle_index === i);
    if (existing) {
      out.push(existing);
      continue;
    }
    const member = waiting.shift();
    out.push({
      cycle_index: i,
      status: "upcoming",
      due_date: addPeriods(o.start_date, i, o.cycle_unit),
      pot_total: "0.00",
      amount_due: "0.00",
      recipient_id: member ? member.member_id : null,
      unpaid_count: 0,
    });
  }
  return out;
}

/** "$1,234.50" -> ["$1,234", ".50"], so the cents can be shown quieter. Plain text only. */
export function splitCents(text: string): [string, string] {
  const i = text.lastIndexOf(".");
  return i > 0 ? [text.slice(0, i), text.slice(i)] : [text, ""];
}

function potOf(o: CircleOverview): string {
  const n = Number(o.contribution_amount) * o.member_count;
  return Number.isFinite(n) ? n.toFixed(2) : "";
}

function MemberRing({ o, viewerId }: { o: CircleOverview; viewerId: MemberId }) {
  const cycles = scheduleOf(o);
  const n = Math.max(cycles.length, 1);
  const names = new Map(o.members.map((m) => [m.member_id, m.display_name]));
  const done = cycles.filter((c) => c.status === "paid_out").length;
  const label = `Payout ring: ${done} of ${cycles.length} members have received the pot.`;
  return (
    <svg className="ring" viewBox="0 0 200 200" role="img" aria-label={label}>
      <circle cx="100" cy="100" r="72" className="ring__track" />
      {cycles.map((c, i) => {
        const a = (i / n) * Math.PI * 2 - Math.PI / 2;
        const x = 100 + 72 * Math.cos(a);
        const y = 100 + 72 * Math.sin(a);
        const stage = stageOf(c, o.current_cycle_index);
        const isYou = c.recipient_id === viewerId;
        const who = c.recipient_id ? names.get(c.recipient_id) ?? "" : "";
        return (
          <g key={c.cycle_index} className={`ring__dot ring__dot--${stage}${isYou ? " ring__dot--you" : ""}`}>
            <circle cx={x} cy={y} r={isYou ? 17 : 14} />
            <text x={x} y={y} dy="0.35em" textAnchor="middle">
              {who ? initialOf(who) : String(c.cycle_index + 1)}
            </text>
          </g>
        );
      })}
      <text x="100" y="94" textAnchor="middle" className="ring__big">
        {Math.min(o.current_cycle_index + 1, cycles.length)}/{cycles.length}
      </text>
      <text x="100" y="118" textAnchor="middle" className="ring__small">
        cycle
      </text>
    </svg>
  );
}

function Timeline({ o, viewerId }: { o: CircleOverview; viewerId: MemberId }) {
  const names = new Map(o.members.map((m) => [m.member_id, m.display_name]));
  const cycles = scheduleOf(o);
  return (
    <section className="card timeline-card" aria-labelledby="timeline-title">
      <div className="card__head">
        <h2 id="timeline-title">Payout schedule</h2>
        <p className="muted small">Each cycle, one member takes the whole pot.</p>
      </div>
      <ol className="timeline">
        {cycles.map((c) => {
          const stage = stageOf(c, o.current_cycle_index);
          const isYou = c.recipient_id === viewerId;
          const who = c.recipient_id ? names.get(c.recipient_id) ?? c.recipient_id : "Not assigned";
          return (
            <li
              key={c.cycle_index}
              className={`timeline__item timeline__item--${stage}${isYou ? " timeline__item--you" : ""}`}
              aria-current={stage === "now" ? "step" : undefined}
            >
              <span className="avatar" aria-hidden="true">
                {initialOf(who)}
              </span>
              <span className="timeline__name" dir="auto" title={who}>
                {who}
                {isYou ? <span className="you-badge">You</span> : null}
              </span>
              <span className="timeline__date">{formatIsoDate(c.due_date)}</span>
              <span className={`stage stage--${stage}`}>{STAGE_TEXT[stage]}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function CircleHero({ circleId, viewerId }: { circleId: string; viewerId: MemberId }) {
  const state = useAsync(() => getCircleOverview(circleId), [circleId]);
  if (state.status === "loading") {
    return <div className="hero hero--loading" role="status" aria-label="Loading the circle" />;
  }
  if (state.status === "error") return null; // the rest of the page still works without it
  const o = state.data;
  const cycles = scheduleOf(o);
  const names = new Map(o.members.map((m) => [m.member_id, m.display_name]));
  const current = cycles.find((c) => c.cycle_index === o.current_cycle_index);
  const mine = cycles.find((c) => c.recipient_id === viewerId);
  const every = o.cycle_unit === "WEEK" ? "week" : "month";
  const pot = potOf(o);
  const allDone = cycles.length > 0 && cycles.every((c) => c.status === "paid_out");

  let turnTitle = "No payout slot yet";
  let turnText = "The fair order assigns one once everyone approves.";
  if (mine) {
    const stage = stageOf(mine, o.current_cycle_index);
    turnTitle =
      stage === "done" ? "You received the pot" : stage === "now" ? "Your pot is this cycle" : "Your turn is coming";
    turnText = `Cycle ${mine.cycle_index + 1}, ${formatIsoDate(mine.due_date)}`;
  }

  return (
    <>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero__main">
          <h2 id="hero-title" className="hero__name" dir="auto">
            {o.name}
          </h2>
          <p className="hero__meta">
            {o.member_count} members · {formatUsd(o.contribution_amount)} each {every}
          </p>
          <div className="hero__pot">
            <span className="hero__pot-label">Pot each {every}</span>
            <span className="hero__pot-value num">
              {pot ? splitCents(formatUsd(pot))[0] : ""}
              <span className="hero__cents">{pot ? splitCents(formatUsd(pot))[1] : ""}</span>
            </span>
          </div>
          <div className="hero__facts">
            <div className="hero__fact">
              <span className="hero__fact-label">{allDone ? "Circle" : "This cycle"}</span>
              <span className="hero__fact-value">
                {allDone
                  ? "Every member has received the pot"
                  : current
                    ? `Pot goes to `
                    : "Not started"}
                {!allDone && current?.recipient_id ? (
                  <bdi>{names.get(current.recipient_id) ?? current.recipient_id}</bdi>
                ) : null}
              </span>
              {!allDone && current ? (
                <span className="hero__fact-sub">Due {formatIsoDate(current.due_date)}</span>
              ) : null}
            </div>
            <div className="hero__fact hero__fact--you">
              <span className="hero__fact-label">Your turn</span>
              <span className="hero__fact-value">{turnTitle}</span>
              <span className="hero__fact-sub">{turnText}</span>
            </div>
          </div>
        </div>
        <MemberRing o={o} viewerId={viewerId} />
      </section>
      <Timeline o={o} viewerId={viewerId} />
    </>
  );
}
