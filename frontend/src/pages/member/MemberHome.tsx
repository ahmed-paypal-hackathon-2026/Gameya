// Member view. Imports nothing that can load or show reliability data (plan RSK-09).
import { AsyncBlock } from "../../components/AsyncBlock";
import { CircleHero } from "../../components/CircleHero";
import { ApproveOrderButton, MyInvoicesCard, NeedForm } from "../../components/MemberActions";
import { OrderApprovalCard } from "../../components/OrderApprovalCard";
import { useOrderApproval } from "../../hooks/useOrderApproval";
import type { MemberId, Viewer } from "../../types/contracts";

interface Props {
  viewer: Viewer;
  names: Partial<Record<MemberId, string>>;
}

export function MemberHome({ viewer, names }: Props) {
  const order = useOrderApproval(viewer.circle_id);
  return (
    <div className="stack">
      <CircleHero circleId={viewer.circle_id} viewerId={viewer.viewer_id} />
      <MyInvoicesCard viewer={viewer} />
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
      <NeedForm viewer={viewer} />
    </div>
  );
}
