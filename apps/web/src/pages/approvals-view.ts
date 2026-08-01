import type { GlobalApproval } from "../api/operations-query-adapter";

export type ApprovalDisplayMeta = {
  title: string;
  riskLabel: string;
  categoryLabel: string;
  deadlineLabel?: string;
};

/**
 * The approval queue renders `/api/v2/approvals` verbatim.  Display metadata is
 * derived from the record rather than supplied alongside it, so a row can never
 * present a label the backend did not justify.
 */
export type ApprovalView = GlobalApproval;

export function approvalMeta(approval: ApprovalView): ApprovalDisplayMeta {
  const riskLabel = approval.risk === "destructive"
    ? "高风险"
    : approval.risk === "active" ? "中风险" : "低风险";
  const categoryLabel = approval.reversibility === "irreversible"
    ? "破坏性"
    : approval.action_kind.includes("network") ? "网络攻击" : "主动操作";
  return { title: approval.capability, riskLabel, categoryLabel };
}
