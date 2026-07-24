import { useState } from "react";
import type { TaskListItem } from "../api/tasks";
import { EmptyState, statusLabel } from "../components/ui/EmptyState";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";

export function DashboardPage({ tasks, onNew, onOpen, onDelete }: { tasks: TaskListItem[]; onNew: () => void; onOpen: (id: string) => void; onDelete: (id: string) => Promise<void> }) {
  const [confirmDelete, setConfirmDelete] = useState<TaskListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const counts = ["running", "paused", "awaiting_approval", "blocked", "completed"].map((status) => ({ status, count: tasks.filter((item) => item.status === status).length }));
  const grouped = Object.fromEntries(TASK_MODES.map((mode) => [mode, tasks.filter((task) => task.mode === mode)])) as Record<TaskMode, TaskListItem[]>;
  const remove = async () => { if (!confirmDelete) return; setDeleting(true); setError(""); try { await onDelete(confirmDelete.task_id); setConfirmDelete(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "删除任务失败"); } finally { setDeleting(false); } };

  return <section className="page-stack dashboard-page">
    <header className="page-title"><div><span className="eyebrow">任务中心</span><h1>任务总览</h1><p>集中查看任务状态、执行轮次、证据产物和最新运行事件。</p></div><button onClick={onNew}>新建任务</button></header>
    <div className="metric-grid">{counts.map((item) => <article className={`metric-card status-${item.status}`} key={item.status}><span>{statusLabel(item.status)}</span><strong>{item.count}</strong><small>{metricHint(item.status)}</small></article>)}</div>
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    <div className="dashboard-scene-stack">{TASK_MODES.map((mode, index) => <section className="surface dashboard-scene-section" key={mode} aria-labelledby={`dashboard-scene-${mode}`}>
      <div className="dashboard-scene-head"><div><span>0{index + 1}</span><div><h2 id={`dashboard-scene-${mode}`}>{MODE_PROFILES[mode].label}</h2><p>{MODE_PROFILES[mode].description}</p></div></div><b>{grouped[mode].length} 个任务</b></div>
      {grouped[mode].length ? <div className="task-card-grid">{grouped[mode].map((task) => <TaskCard key={task.task_id} task={task} onOpen={onOpen} onDelete={setConfirmDelete} />)}</div> : <EmptyState label={`“${MODE_PROFILES[mode].label}”场景暂无任务。`} />}
    </section>)}</div>
    {confirmDelete ? <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-task-title"><h2 id="delete-task-title">删除历史任务？</h2><p>将永久删除“{confirmDelete.name || confirmDelete.task_id}”的任务会话、运行事件、证据产物和报告，无法恢复。</p><div><button className="secondary-button" disabled={deleting} onClick={() => setConfirmDelete(null)}>返回</button><button className="danger-button" disabled={deleting} onClick={() => void remove()}>{deleting ? "正在删除…" : "确认删除"}</button></div></section></div> : null}
  </section>;
}

function TaskCard({ task, onOpen, onDelete }: { task: TaskListItem; onOpen: (id: string) => void; onDelete: (task: TaskListItem) => void }) {
  const turns = task.turn_count ?? 0;
  const maxTurns = task.max_turns ?? 0;
  const progress = maxTurns ? Math.min(100, Math.round(turns / maxTurns * 100)) : 0;
  const entry = task.task_entry_url || task.target_summary || "本地输入任务";
  return <article className="task-card"><button className="task-card-open" onClick={() => onOpen(task.task_id)}><div className="task-card-title"><span className={`status-badge ${task.status}`}>{statusLabel(task.status)}</span><small>{task.task_id}</small></div><h3>{task.name || task.task_id}</h3><p title={entry}>{entry}</p><div className="task-health"><span><b>{task.active_solvers ?? 0}</b>执行单元</span><span><b>{task.artifacts}</b>证据产物</span>{task.mode === "ctf" ? <span><b>{task.flags}</b>已确认 Flag</span> : null}<span><b>{task.findings}</b>已确认发现</span></div><div className="budget-row"><span>执行轮次 {turns}/{maxTurns || "—"}</span><span>{progress}%</span></div><div className="budget-track"><i style={{ width: `${progress}%` }} /></div><small className="latest-event">{task.latest_event ? `事件 #${task.latest_event.seq ?? "—"} · ${eventTypeLabel(task.latest_event.type ?? "")}` : "尚无运行事件"}</small></button><footer><small>{task.updated_at ? new Date(task.updated_at).toLocaleString("zh-CN") : "等待更新"}</small><button className="task-delete danger-button" disabled={task.status === "running"} title={task.status === "running" ? "运行中的任务需先取消" : "删除历史任务"} onClick={() => onDelete(task)}>删除</button></footer></article>;
}

function metricHint(status: string) { return ({ running: "正在执行受控动作", paused: "等待人工继续", awaiting_approval: "等待审批高影响动作", blocked: "需要提示或策略调整", completed: "已通过服务端完成判定" } as Record<string, string>)[status] ?? ""; }
function eventTypeLabel(type: string) { return ({ PROVIDER_RESPONSE_DISCARDED: "模型响应已丢弃", SESSION_STOPPED: "任务会话已停止", HTTP_SESSION_STATUS: "HTTP 会话状态", FINISH_ACCEPTED: "完成校验通过", AGENT_FINISHED: "任务执行结束", TOOL_EXECUTION_END: "工具执行结束", RUNTIME_ERROR: "运行时错误" } as Record<string, string>)[type] ?? (type || "未知事件"); }
