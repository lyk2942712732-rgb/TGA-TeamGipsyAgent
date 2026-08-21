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
    {approvals.length ? <div className="approval-center-list">{approvals.map((approval) => {
      const intent = approval.intentId ? store.intentsById[approval.intentId] : null;
      const args = objectValue(approval.action.arguments);
      return <article key={approval.approvalId}><header><div><StatusBadge value={approval.status} /><h4>{String(approval.action.capability ?? approval.actionId)}</h4></div><time>{approval.deadline || "本次调用有效"}</time></header><dl><Item label="Solver" value={approval.solverId} /><Item label="Intent" value={intent?.title ?? approval.intentId ?? "Task"} /><Item label="执行目标" value={String(approval.action.target ?? "任务范围内能力")} /><Item label="预期结果" value={String(approval.action.expected_outcome ?? "返回一次受治理的工具结果")} /><Item label="风险等级" value={approval.risk} /><Item label="影响范围" value={String(approval.effect.description ?? "任务范围内操作")} /><Item label="可逆性" value={String(approval.effect.reversibility ?? "未声明")} /></dl>{Object.keys(args).length ? <section className="approval-arguments"><b>本次参数</b><pre>{JSON.stringify(args, null, 2)}</pre></section> : null}<p><b>审批原因</b>{approval.reason}</p><p><b>可选方案</b>{approval.alternatives.join("；") || "拒绝后由 Worker 重新选择路径"}</p>{!readonly ? <footer><button className="danger" disabled={busy !== null} aria-label={`拒绝 ${approval.actionId}`} onClick={() => void decide(approval, "reject")}>拒绝</button><button className="primary" disabled={busy !== null} aria-label={`批准 ${approval.actionId}`} onClick={() => void decide(approval, "approve")}>批准本次调用</button></footer> : <small>Replay 模式不可发送审批决定</small>}</article>;
    })}</div> : <p className="runtime-empty">没有待审批操作</p>}
  </section>;
}

function Item({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function objectValue(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
