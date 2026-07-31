import { useEffect, useRef, useState } from "react";
import { ChevronDown, Lock } from "lucide-react";
import { runtimeApi } from "../../../runtime/api-v2";
import { selectPendingApprovals } from "../models/selectors";
import type { RuntimeStore } from "../models/types";
import type { RuntimeConnection } from "../use-task-runtime";
import { StatusBadge } from "../../../shared/StatusBadge";

/**
 * Reference image 05's Mission Control header: a plain page header (no card)
 * whose first line is the title and the 任务操作 menu, and whose second line is
 * the task identity plus start time, elapsed time and overall progress.
 *
 * The reference collapses every task control into that one menu — the always-on
 * buttons it draws instead live in the bottom action dock.
 */

type Control = "pause" | "resume" | "cancel";

export function TaskCommandHeader({ store, connection, mode, busy = false, onControl = () => undefined, onIntervention = () => undefined, onApprovals = () => undefined, onReplay = () => undefined }: { store: RuntimeStore; connection: RuntimeConnection; mode: "runtime" | "replay"; busy?: boolean; onControl?: (action: Control) => void; onIntervention?: () => void; onApprovals?: () => void; onReplay?: () => void }) {
  const solvers = Object.values(store.solversById);
  const intents = Object.values(store.intentsById);
  const completedIntents = intents.filter((intent) => intent.status === "completed").length;
  const completedSolvers = solvers.filter((solver) => solver.status === "completed").length;
  const blockedSolvers = solvers.filter((solver) => ["blocked", "failed", "awaiting_approval"].includes(solver.status)).length;
  const approvals = selectPendingApprovals(store).length;
  const usage = store.session.taskBudgetUsage;
  const readonly = mode === "replay";
  const percent = intents.length ? Math.round(completedIntents / intents.length * 100) : 0;

  return <header className="mission-header">
    <div className="mission-header-top">
      <h1>运行页 / Mission Control</h1>
      <TaskActionMenu
        store={store}
        readonly={readonly}
        busy={busy}
        approvals={approvals}
        mode={mode}
        onControl={onControl}
        onIntervention={onIntervention}
        onApprovals={onApprovals}
        onReplay={onReplay}
      />
    </div>

    <div className="mission-meta">
      <span className="mission-meta-task">任务:<h2>{store.task.name}</h2></span>
      <StatusBadge value={store.session.status} icon={<Lock size={11} aria-hidden="true" />} />
      <span><i>开始时间:</i> {formatStart(store.session.timestamps.started_at)}</span>
      <span><i>运行时长:</i> {elapsed(store.session.timestamps.started_at, store.session.timestamps.finished_at)}</span>
      <span className="mission-progress">
        <i>进度:</i> <b>{percent}%</b>
        <progress aria-label="总体进度" max={Math.max(1, intents.length)} value={completedIntents} />
      </span>
    </div>

    {/* Kept from the aggregate strip the reference leaves out: these are the
        only place the task-level budget and stream state are visible. */}
    <dl className="mission-stats">
      <Metric label="Solver" value={`${store.session.activeSolverCount} 活动 / ${completedSolvers} 完成 / ${blockedSolvers} 阻塞`} />
      <Metric label="Token" value={`${(usage.input_tokens ?? 0) + (usage.output_tokens ?? 0)}`} />
      <Metric label="Tool" value={`${usage.tool_calls ?? 0}`} />
      <Metric label="Artifact" value={`${usage.artifacts ?? Object.keys(store.artifactsById).length}`} />
      <Metric label="待审批" value={`${approvals}`} />
      <Metric label="Intent" value={`${completedIntents}/${intents.length}`} />
      <Metric label="SSE" value={connectionLabel(connection)} />
    </dl>
  </header>;
}

function TaskActionMenu({ store, readonly, busy, approvals, mode, onControl, onIntervention, onApprovals, onReplay }: {
  store: RuntimeStore;
  readonly: boolean;
  busy: boolean;
  approvals: number;
  mode: "runtime" | "replay";
  onControl: (action: Control) => void;
  onIntervention: () => void;
  onApprovals: () => void;
  onReplay: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const run = (action: () => void) => () => { setOpen(false); action(); };
  const status = store.session.status;

  return <div className="mission-action-menu" ref={root}>
    <button className="ref-primary-button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((value) => !value)}>
      任务操作 <ChevronDown size={15} aria-hidden="true" />
    </button>
    {open ? <div className="mission-action-list" role="menu">
      {!readonly && status === "running"
        ? <button role="menuitem" disabled={busy} onClick={run(() => onControl("pause"))}>暂停全部</button> : null}
      {!readonly && ["paused", "blocked"].includes(status)
        ? <button role="menuitem" disabled={busy} onClick={run(() => onControl("resume"))}>恢复全部</button> : null}
      {!readonly ? <button role="menuitem" onClick={run(onIntervention)}>补充信息</button> : null}
      <button role="menuitem" onClick={run(onApprovals)}>审批中心 {approvals ? `(${approvals})` : ""}</button>
      {mode === "runtime" ? <button role="menuitem" onClick={run(onReplay)}>回放</button> : null}
      <a role="menuitem" href={runtimeApi.reportUrl(store.task.id)} target="_blank" rel="noreferrer">报告</a>
      {!readonly && !["completed", "cancelled", "failed"].includes(status)
        ? <button role="menuitem" className="danger" disabled={busy} onClick={run(() => onControl("cancel"))}>取消任务</button> : null}
    </div> : null}
  </div>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function connectionLabel(value: RuntimeConnection) { return ({ loading: "连接中", live: "实时", reconnecting: "重连中", offline: "离线" } as Record<RuntimeConnection, string>)[value]; }
function formatStart(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).replace(/\//g, "-");
}
function elapsed(start?: string | null, finish?: string | null) { if (!start) return "-"; const from = new Date(start).getTime(); const to = finish ? new Date(finish).getTime() : Date.now(); if (!Number.isFinite(from) || !Number.isFinite(to)) return "-"; const seconds = Math.max(0, Math.round((to - from) / 1000)); return seconds >= 3600 ? `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟` : seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`; }
