// Order-approval card: every member sees each member's extracted need (category and date)
// so an exaggerated need can be spotted before everyone approves (issue #5).
// Deliberately takes no reliability data: it is shown to members.
import type { ReactNode } from "react";
import type { MemberId, NeedExtraction, OrderProposal } from "../types/contracts";
import { NEED_CATEGORY_LABEL, formatCost, formatIsoDate } from "../lib/format";

export interface OrderApprovalCardProps {
  proposal: OrderProposal;
  /** member_id -> display name. Names are member-chosen text and are shown as plain text. */
  names: Partial<Record<MemberId, string>>;
  needs: Partial<Record<MemberId, NeedExtraction>>;
  /** True when the needs could not be loaded, so "no need" is not claimed wrongly. */
  needsUnavailable?: boolean;
  viewerId?: MemberId;
  footer?: ReactNode;
}

function needText(need: NeedExtraction | undefined, unavailable: boolean): string {
  if (!need) return unavailable ? "Need not available" : "No need submitted";
  const category = NEED_CATEGORY_LABEL[need.category] ?? need.category;
  return need.target_date
    ? `${category}, needed by ${formatIsoDate(need.target_date)}`
    : `${category}, no date given`;
}

export function OrderApprovalCard({
  proposal,
  names,
  needs,
  needsUnavailable = false,
  viewerId,
  footer,
}: OrderApprovalCardProps) {
  const assignments = [...proposal.assignments].sort((a, b) => a.slot_index - b.slot_index);
  const total = assignments.length;

  return (
    <section className="card" aria-labelledby="order-approval-title">
      <h2 id="order-approval-title">Proposed payout order</h2>
      <p className="muted">
        Needs are self-reported. Check that each one looks right before you approve. Need cost
        shows how far a slot is from what the member asked for; lower is a closer fit.
      </p>
      {needsUnavailable ? (
        <p className="notice" role="status">
          Members' needs could not be loaded, so they are not shown below.
        </p>
      ) : null}
      <ol className="order-list">
        {assignments.map((a) => {
          const name = names[a.member_id] ?? a.member_id;
          const explanation = proposal.explanations[a.member_id];
          const isViewer = a.member_id === viewerId;
          return (
            <li
              key={a.member_id}
              className={isViewer ? "order-item order-item--viewer" : "order-item"}
              data-testid={`order-item-${a.member_id}`}
            >
              <div className="order-item__head">
                <span className="order-item__slot">
                  Slot {a.slot_index + 1} of {total}
                </span>
                <span className="order-item__name" dir="auto" title={name}>
                  {name}
                </span>
                {isViewer ? <span className="tag">You</span> : null}
                <span className="order-item__date">Payout {formatIsoDate(a.payout_date)}</span>
              </div>
              <dl className="order-item__facts">
                <div>
                  <dt>Need</dt>
                  <dd data-testid={`need-${a.member_id}`}>
                    {needText(needs[a.member_id], needsUnavailable)}
                  </dd>
                </div>
                <div>
                  <dt>Need cost</dt>
                  <dd className="num">{formatCost(a.cost)}</dd>
                </div>
              </dl>
              {explanation ? (
                // Plain text: never rendered as HTML or markdown.
                <p className="order-item__why" dir="auto">
                  {explanation}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
      {footer}
    </section>
  );
}
