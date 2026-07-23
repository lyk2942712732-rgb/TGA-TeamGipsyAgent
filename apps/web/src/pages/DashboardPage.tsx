import { useState } from "react";
import type { TaskListItem } from "../api/tasks";
import { EmptyState, statusLabel } from "../components/ui/EmptyState";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";

export function DashboardPage({ tasks, onNew, onOpen, onDelete }: { tasks: TaskListItem[]; onNew: () => void; onOpen: (id: string) => void; onDelete: (id: string) => Promise<void> }) {
  const [confirmDelete, setConfirmDelete] = useState<TaskListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const counts = ["running", "paused", "blocked", "completed"].map((status) => ({ status, count: tasks.filter((item) => item.status === status).length }));
  const grouped = Object.fromEntries(TASK_MODES.map((mode) => [mode, tasks.filter((task) => task.mode === mode)])) as Record<TaskMode, TaskListItem[]>;
  const remove = async () => { if (!confirmDelete) return; setDeleting(true); setError(""); try { await onDelete(confirmDelete.task_id); setConfirmDelete(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除任务失败"); } finally { setDeleting(false); } };

  return <section className="page-stack dashboard-page">
    <header className="page-title"><div><span className="eyebrow">TGA / Agent 任务</span><h1>任务总览</h1><p>统一观察 Session、Solver 工具调用和最新运行事件。</p></div><button onClick={onNew}>新建任务</button></header>
    <div className="metric-grid">{counts.map((item) => <article className={`metric-card status-${item.status}`} key={item.status}><span>{statusLabel(item.status)}</span><strong>{item.count}</strong><small>{metricHint(item.status)}</small></article>)}</div>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    <div className="dashboard-scene-stack">{TASK_MODES.map((mode, index) => <section className="surface dashboard-scene-section" key={mode} aria-labelledby={`dashboard-scene-${mode}`}>
      <div className="dashboard-scene-head"><div><span>0{index + 1}</span><div><h2 id={`dashboard-scene-${mode}`}>{MODE_PROFILES[mode].label}</h2><p>{MODE_PROFILES[mode].description}</p></div></div><b>{grouped[mode].length} 个任务</b></div>
      {grouped[mode].length ? <div className="task-card-grid">{grouped[mode].map((task) => <TaskCard key={task.task_id} task={task} onOpen={onOpen} onDelete={setConfirmDelete} />)}</div> : <EmptyState label={`“${MODE_PROFILES[mode].label}”场景暂无任务。`} />}
    </section>)}</div>
    {confirmDelete ? <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-task-title"><h2 id="delete-task-title">删除历史任务？</h2><p>将永久删除“{confirmDelete.name || confirmDelete.task_id}”的会话、事件、Artifact 和报告，无法恢复。</p><div><button className="secondary-button" disabled={deleting} onClick={() => setConfirmDelete(null)}>返回</button><button className="danger-button" disabled={deleting} onClick={() => void remove()}>{deleting ? "正在删除…" : "确认删除"}</button></div></section></div> : null}
  </section>;
}

function TaskCard({ task, onOpen, onDelete }: { task: TaskListItem; onOpen: (id: string) => void; onDelete: (task: TaskListItem) => void }) {
  const turns = task.turn_count ?? 0;
  const maxTurns = task.max_turns ?? 0;
  const progress = maxTurns ? Math.min(100, Math.round(turns / maxTurns * 100)) : 0;
  return <article className="task-card"><button className="task-card-open" onClick={() => onOpen(task.task_id)}><div className="task-card-title"><span className={`status-badge ${task.status}`}>{statusLabel(task.status)}</span></div><h3>{task.name || task.task_id}</h3><p title={task.target}>{task.target}</p><div className="task-health"><span><b>{task.active_solvers ?? 0}</b> Solver</span><span><b>{task.artifacts}</b> Artifact</span>{task.mode === "ctf" ? <span><b>{task.flags}</b> Flag</span> : null}<span><b>{task.findings}</b> Finding</span></div><div className="budget-row"><span>Agent 回合 {turns}/{maxTurns || "—"}</span><span>{progress}%</span></div><div className="budget-track"><i style={{ width: `${progress}%` }} /></div><small className="latest-event">{task.latest_event ? `#${task.latest_event.seq ?? "—"} ${task.latest_event.type ?? "event"}` : "尚无运行事件"}</small></button><footer><small>{task.updated_at ? new Date(task.updated_at).toLocaleString() : "等待更新"}</small><button className="task-delete danger-button" disabled={task.status === "running"} title={task.status === "running" ? "运行中的任务需先取消" : "删除历史任务"} onClick={() => onDelete(task)}>删除</button></footer></article>;
}

function metricHint(status: string) { return ({ running: "正在执行受控动作", paused: "等待人工继续", blocked: "需要提示或策略调整", completed: "已通过服务端完成判定" } as Record<string, string>)[status] ?? ""; }
