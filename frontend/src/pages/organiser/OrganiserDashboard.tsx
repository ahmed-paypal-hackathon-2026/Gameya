import { useState } from "react";
import { getAudit, getCircle, getLedger, getReliability, runStressTest } from "../../api/client";
import { AsyncBlock } from "../../components/AsyncBlock";
import { CycleActions } from "../../components/CycleActions";
import { ApproveOrderButton, MyInvoicesCard, NeedForm } from "../../components/MemberActions";
import { OrderApprovalCard } from "../../components/OrderApprovalCard";
import { OrganiserOnly } from "../../components/OrganiserOnly";
import { AuditGrid } from "../../grids/AuditGrid";
import { LedgerGrid } from "../../grids/LedgerGrid";
import { ReliabilityGrid } from "../../grids/ReliabilityGrid";
import { StressTestGrid } from "../../grids/StressTestGrid";
import { useAsync } from "../../hooks/useAsync";
import { useOrderApproval } from "../../hooks/useOrderApproval";
import type { MemberId, Viewer } from "../../types/contracts";
import { Chat } from "./Chat";

interface Props {
  viewer: Viewer;
  names: Partial<Record<MemberId, string>>;
}

function ReliabilitySection({ viewer }: { viewer: Viewer }) {
  const state = useAsync(() => getReliability(viewer), [viewer.viewer_id, viewer.circle_id]);
  return (
    <section className="card" aria-labelledby="reliability-title">
      <h2 id="reliability-title">Payment reliability</h2>
      <p className="muted">
        Only you can see this. Red factors raise a member's chance of paying late; green
        factors lower it. Members never see their own or anyone else's score.
      </p>
      <AsyncBlock state={state} loadingText="Loading reliability scores...">
        {(data) => <ReliabilityGrid rows={data.rows} />}
      </AsyncBlock>
    </section>
  );
}

function StressTestSection({ viewer }: { viewer: Viewer }) {
  const state = useAsync(() => runStressTest(viewer.circle_id), [viewer.circle_id]);
  return (
    <section className="card" aria-labelledby="stress-title">
      <h2 id="stress-title">Reserve stress test</h2>
      <AsyncBlock state={state} loadingText="Running the stress test...">
        {(result) => <StressTestGrid result={result} />}
      </AsyncBlock>
    </section>
  );
}

function MoneySections({ viewer, names }: Props) {
  // Bumped after every cycle action so the ledger and audit show the new rows.
  const [version, setVersion] = useState(0);
  const circle = useAsync(() => getCircle(viewer.circle_id), [viewer.circle_id, version]);
  const ledger = useAsync(() => getLedger(viewer), [viewer.viewer_id, viewer.circle_id, version]);
  const audit = useAsync(() => getAudit(viewer), [viewer.viewer_id, viewer.circle_id, version]);
  return (
    <>
      <AsyncBlock state={circle} loadingText="Loading the cycles...">
        {(c) => <CycleActions circle={c} names={names} onChanged={() => setVersion((v) => v + 1)} />}
      </AsyncBlock>
      <section className="card" aria-labelledby="ledger-title">
        <h2 id="ledger-title">Ledger</h2>
        <p className="muted">Every amount here comes from PayPal: invoice ids and payout batch ids.</p>
        <AsyncBlock state={ledger} loadingText="Loading the ledger...">
          {(data) => <LedgerGrid data={data} names={names} />}
        </AsyncBlock>
      </section>
      <section className="card" aria-labelledby="audit-title">
        <h2 id="audit-title">AI audit log</h2>
        <p className="muted">Every AI decision, newest first. Click a row for the detail.</p>
        <AsyncBlock state={audit} loadingText="Loading the audit log...">
          {(data) => <AuditGrid decisions={data.decisions} />}
        </AsyncBlock>
      </section>
    </>
  );
}

export function OrganiserDashboard({ viewer, names }: Props) {
  const order = useOrderApproval(viewer.circle_id);
  return (
    <div className="stack">
      <OrganiserOnly viewer={viewer}>
        <MoneySections viewer={viewer} names={names} />
        <StressTestSection viewer={viewer} />
        <ReliabilitySection viewer={viewer} />
      </OrganiserOnly>
      <MyInvoicesCard viewer={viewer} />
      <NeedForm viewer={viewer} />
      <AsyncBlock state={order} loadingText="Loading the proposed order...">
        {(d) => (
          <OrderApprovalCard
            proposal={d.proposal}
            names={names}
            needs={d.needs}
            needsUnavailable={d.needsUnavailable}
            viewerId={viewer.viewer_id}
            footer={<ApproveOrderButton viewer={viewer} />}
          />
        )}
      </AsyncBlock>
      <OrganiserOnly viewer={viewer}>
        <Chat />
      </OrganiserOnly>
    </div>
  );
}
