import { selectPendingApprovals } from "../runtime/models/selectors";
import type { RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

export function ApprovalQueue({ store }: { store: RuntimeStore }) {
  const approvals = selectPendingApprovals(store);
  return <section aria-labelledby="approval-title"><header className="runtime-section-title"><div><span>GOVERNANCE</span><h3 id="approval-title">待审批队列</h3></div><small>{approvals.length} 项</small></header>
    {approvals.length ? <ul className="runtime-approval-list">{approvals.map((approval) => <li key={approval.approvalId}><div><StatusBadge value={approval.status} /><b>{String(approval.action.capability ?? approval.actionId)}</b><p>{approval.reason}</p><small>{approval.solverId} · {approval.intentId ?? "Task"} · {approval.deadline || "无截止时间"}</small></div></li>)}</ul> : <p className="runtime-empty">没有待审批操作</p>}
  </section>;
}
