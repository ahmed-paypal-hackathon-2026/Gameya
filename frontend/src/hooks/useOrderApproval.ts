import { getCircleNeeds, getOrderProposal } from "../api/client";
import type { CircleId, MemberId, NeedExtraction, OrderProposal } from "../types/contracts";
import { useAsync } from "./useAsync";

export interface OrderApprovalData {
  proposal: OrderProposal;
  needs: Partial<Record<MemberId, NeedExtraction>>;
  needsUnavailable: boolean;
}

/** The proposal is required; if only the needs fail, the card still renders and says so. */
export function useOrderApproval(circleId: CircleId) {
  return useAsync<OrderApprovalData>(async () => {
    const [proposal, needs] = await Promise.all([
      getOrderProposal(circleId),
      getCircleNeeds(circleId).catch(() => null),
    ]);
    return { proposal, needs: needs ?? {}, needsUnavailable: needs === null };
  }, [circleId]);
}
