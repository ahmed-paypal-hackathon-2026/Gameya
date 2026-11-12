import { type KeyboardEvent, type ReactNode, useState } from "react";
import { getAudit, getCircle, getLedger, getReliability, runStressTest } from "../../api/client";
import { AsyncBlock } from "../../components/AsyncBlock";
import { CircleHero } from "../../components/CircleHero";
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

function useMoney(viewer: Viewer) {
  // Bumped after every cycle action so the ledger, audit and circle show the new rows.
  const [version, setVersion] = useState(0);
  const circle = useAsync(() => getCircle(viewer.circle_id), [viewer.circle_id, version]);
  const ledger = useAsync(() => getLedger(viewer), [viewer.viewer_id, viewer.circle_id, version]);
  const audit = useAsync(() => getAudit(viewer), [viewer.viewer_id, viewer.circle_id, version]);
  return { circle, ledger, audit, version, bump: () => setVersion((v) => v + 1) };
}

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "money", label: "Money" },
  { id: "risk", label: "Risk" },
  { id: "ai", label: "AI agents" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function Tabs({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const i = TABS.findIndex((t) => t.id === tab);
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = TABS[(i + step + TABS.length) % TABS.length].id;
    onTab(next);
    document.getElementById(`tab-${next}`)?.focus(); // focus follows the selected tab
  }
  return (
    <div className="tabs" role="tablist" aria-label="Organiser sections" onKeyDown={onKey}>
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          id={`tab-${t.id}`}
          aria-selected={tab === t.id}
          aria-controls={`panel-${t.id}`}
          tabIndex={tab === t.id ? 0 : -1}
          className={tab === t.id ? "tab tab--on" : "tab"}
          onClick={() => onTab(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Panel({ id, tab, children }: { id: TabId; tab: TabId; children: ReactNode }) {
  // Every panel stays mounted (data loads once, grids keep state); inactive ones are hidden.
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} hidden={tab !== id} className="stack">
      {children}
    </div>
  );
}

export function OrganiserDashboard({ viewer, names }: Props) {
  const order = useOrderApproval(viewer.circle_id);
  const money = useMoney(viewer);
  const [tab, setTab] = useState<TabId>("overview");
  const orderCard = (
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
  );
  return (
    <div className="stack">
      <CircleHero key={`hero-${money.version}`} circleId={viewer.circle_id} viewerId={viewer.viewer_id} />
      <OrganiserOnly viewer={viewer}>
        <Tabs tab={tab} onTab={setTab} />
        <Panel id="overview" tab={tab}>
          <AsyncBlock state={money.circle} loadingText="Loading the cycles...">
            {(c) => <CycleActions circle={c} names={names} onChanged={money.bump} />}
          </AsyncBlock>
          <MyInvoicesCard viewer={viewer} />
          {orderCard}
          <NeedForm viewer={viewer} />
        </Panel>
        <Panel id="money" tab={tab}>
          <section className="card" aria-labelledby="ledger-title">
            <div className="card__head">
              <h2 id="ledger-title">Ledger</h2>
              <p className="muted">Every amount here comes from PayPal: invoice ids and payout batch ids.</p>
            </div>
            <AsyncBlock state={money.ledger} loadingText="Loading the ledger...">
              {(data) => <LedgerGrid data={data} names={names} />}
            </AsyncBlock>
          </section>
        </Panel>
        <Panel id="risk" tab={tab}>
          <StressTestSection viewer={viewer} />
          <ReliabilitySection viewer={viewer} />
        </Panel>
        <Panel id="ai" tab={tab}>
          <Chat />
          <section className="card" aria-labelledby="audit-title">
            <div className="card__head">
              <h2 id="audit-title">AI audit log</h2>
              <p className="muted">Every AI decision, newest first. Click a row for the detail.</p>
            </div>
            <AsyncBlock state={money.audit} loadingText="Loading the audit log...">
              {(data) => <AuditGrid decisions={data.decisions} />}
            </AsyncBlock>
          </section>
        </Panel>
      </OrganiserOnly>
    </div>
  );
}
