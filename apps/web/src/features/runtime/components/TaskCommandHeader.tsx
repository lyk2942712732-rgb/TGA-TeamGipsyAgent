import { runtimeApi } from "../../../runtime/api-v2";
import { selectPendingApprovals } from "../models/selectors";
import type { RuntimeStore } from "../models/types";
import type { RuntimeConnection } from "../use-task-runtime";
import { StatusBadge } from "../../../shared/StatusBadge";

type Control = "pause" | "resume" | "cancel";
export function TaskCommandHeader({ store, connection, mode, busy = false, onControl = () => undefined, onIntervention = () => undefined, onApprovals = () => undefined, onReplay = () => undefined }: { store: RuntimeStore; connection: RuntimeConnection; mode: "runtime" | "replay"; busy?: boolean; onControl?: (action: Control) => void; onIntervention?: () => void; onApprovals?: () => void; onReplay?: () => void }) {
  const solvers = Object.values(store.solversById);
  const intents = Object.values(store.intentsById);
  const completedIntents = intents.filter((intent) => intent.status === "completed").length;
  const completedSolvers = solvers.filter((solver) => solver.status === "completed").length;
  const blockedSolvers = solvers.filter((solver) => ["blocked", "failed", "awaiting_approval"].includes(solver.status)).length;
  const approvals = selectPendingApprovals(store).length;
  const usage = store.session.taskBudgetUsage;
  const readonly = mode === "replay" || store.legacy;
  return <header className="task-command-header"><div className="command-heading"><span className="runtime-eyebrow">{store.legacy ? "LEGACY REPLAY" : "TASK COMMAND"}</span><h1>{store.task.name}</h1><p>{store.task.goal || store.task.id}</p><div><span>{store.task.mode}</span><StatusBadge value={store.session.status} /></div></div><div className="command-aggregate"><div className="command-progress-line"><span>总体进度</span><progress aria-label="总体进度" max={Math.max(1, intents.length)} value={completedIntents} /><b>{completedIntents}/{intents.length} Intent</b></div><dl><Metric label="Solver" value={`${store.session.activeSolverCount} 活动 / ${completedSolvers} 完成 / ${blockedSolvers} 阻塞`} /><Metric label="Token" value={`${(usage.input_tokens ?? 0) + (usage.output_tokens ?? 0)}`} /><Metric label="Tool" value={`${usage.tool_calls ?? 0}`} /><Metric label="Artifact" value={`${usage.artifacts ?? Object.keys(store.artifactsById).length}`} /><Metric label="待审批" value={`${approvals}`} /><Metric label="运行时间" value={elapsed(store.session.timestamps.started_at, store.session.timestamps.finished_at)} /><Metric label="SSE" value={connectionLabel(connection)} /></dl></div><nav className="command-actions" aria-label="Task 命令">{!readonly && store.session.status === "running" ? <button disabled={busy} onClick={() => onControl("pause")}>暂停全部</button> : null}{!readonly && ["paused", "blocked"].includes(store.session.status) ? <button disabled={busy} onClick={() => onControl("resume")}>恢复全部</button> : null}{!readonly && !["completed", "cancelled", "failed"].includes(store.session.status) ? <button className="danger" disabled={busy} onClick={() => onControl("cancel")}>取消任务</button> : null}{!readonly ? <button onClick={onIntervention}>补充信息</button> : null}<button onClick={onApprovals}>审批中心 {approvals ? `(${approvals})` : ""}</button>{mode === "runtime" ? <button onClick={onReplay}>回放</button> : null}<a href={runtimeApi.reportUrl(store.task.id)} target="_blank" rel="noreferrer">报告</a></nav></header>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function connectionLabel(value: RuntimeConnection) { return ({ loading: "连接中", live: "实时", reconnecting: "重连中", offline: "离线" } as Record<RuntimeConnection, string>)[value]; }
function elapsed(start?: string | null, finish?: string | null) { if (!start) return "-"; const from = new Date(start).getTime(); const to = finish ? new Date(finish).getTime() : Date.now(); if (!Number.isFinite(from) || !Number.isFinite(to)) return "-"; const seconds = Math.max(0, Math.round((to - from) / 1000)); return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`; }
