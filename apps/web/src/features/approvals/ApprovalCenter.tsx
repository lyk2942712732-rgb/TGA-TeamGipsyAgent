import { useState } from "react";
import { runtimeApi } from "../../runtime/api-v2";
import { selectPendingApprovals } from "../runtime/models/selectors";
import type { RuntimeApproval, RuntimeStore } from "../runtime/models/types";
import { StatusBadge } from "../../shared/StatusBadge";

export function ApprovalCenter({ store, readonly, onChanged }: { store: RuntimeStore; readonly: boolean; onChanged: () => void }) {
  const approvals = selectPendingApprovals(store);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const decide = async (approval: RuntimeApproval, decision: "approve" | "reject") => {
    setBusy(approval.actionId); setMessage(null);
    try { await runtimeApi.approvalDecision(store.task.id, approval.actionId, decision); setMessage(decision === "approve" ? "已提交一次性批准" : "已拒绝该操作"); onChanged(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "审批失败"); }
    finally { setBusy(null); }
  };
  return <section className="approval-center" aria-labelledby="approval-center-title"><header className="runtime-section-title"><div><span>APPROVAL CENTER</span><h3 id="approval-center-title">审批中心</h3></div><small>{approvals.length} 项待处理</small></header>{message ? <p role="status">{message}</p> : null}
    {approvals.length ? <div className="approval-center-list">{approvals.map((approval) => <article key={approval.approvalId}><header><div><StatusBadge value={approval.status} /><h4>{String(approval.action.capability ?? approval.actionId)}</h4></div><time>{approval.deadline || "无截止时间"}</time></header><dl><Item label="Solver" value={approval.solverId} /><Item label="Intent" value={approval.intentId ?? "Task"} /><Item label="Target" value={String(approval.action.target ?? "未投影")} /><Item label="Expected outcome" value={String(approval.action.expected_outcome ?? "未投影")} /><Item label="Risk" value={approval.risk} /><Item label="Effect" value={String(approval.effect.description ?? "未投影")} /><Item label="Reversibility" value={String(approval.effect.reversibility ?? "未投影")} /></dl><p><b>Reason</b>{approval.reason}</p><p><b>Alternatives</b>{approval.alternatives.join("；") || "未提供"}</p>{!readonly ? <footer><button className="danger" disabled={busy !== null} aria-label={`拒绝 ${approval.actionId}`} onClick={() => void decide(approval, "reject")}>拒绝</button><button className="primary" disabled={busy !== null} aria-label={`批准 ${approval.actionId}`} onClick={() => void decide(approval, "approve")}>批准一次</button></footer> : <small>Replay 模式不可发送审批决定</small>}</article>)}</div> : <p className="runtime-empty">没有待审批操作</p>}
  </section>;
}

function Item({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
