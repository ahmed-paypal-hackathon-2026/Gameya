// Member view. Imports nothing that can load or show reliability data (plan RSK-09).
import { AsyncBlock } from "../../components/AsyncBlock";
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
      <AsyncBlock state={order} loadingText="Loading the proposed order...">
        {(d) => (
          <OrderApprovalCard
            proposal={d.proposal}
            names={names}
            needs={d.needs}
            needsUnavailable={d.needsUnavailable}
            viewerId={viewer.viewer_id}
          />
        )}
      </AsyncBlock>
    </div>
  );
}
