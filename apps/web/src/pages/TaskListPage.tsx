import { useQuery } from "@tanstack/react-query";
import { Grid2X2, List, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { deleteTask, listTasks, type TGA3TaskListItem } from "../api/tga3-tasks";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { MODE_PROFILES, TASK_MODES } from "../modes";
import { statusLabel } from "../shared/status";

const STATES = ["created", "starting", "running", "waiting_user", "finalizing", "reporting", "completed", "failed", "cancelled"];
const STATE_TONES: Record<string, string> = { running: "tone-ok", completed: "tone-ok", waiting_user: "tone-warn", finalizing: "tone-info", reporting: "tone-info", failed: "tone-danger", cancelled: "tone-muted", starting: "tone-info", created: "tone-muted" };

export function TaskListPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [removeTask, setRemoveTask] = useState<TGA3TaskListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [message, setMessage] = useState("");
  const query = params.get("query") ?? "";
  const scene = params.get("scene") ?? "";
  const state = params.get("state") ?? "";
  const view = params.get("view") === "cards" ? "cards" : "list";
  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const rows = useMemo(() => (tasks.data ?? []).filter((task) =>
    (!query || `${task.title} ${task.id}`.toLowerCase().includes(query.toLowerCase())) &&
    (!scene || task.scene_id === scene) && (!state || task.state === state),
  ), [tasks.data, query, scene, state]);
  const visible = usePage(rows, pageSize, page);
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next); setPage(1); };
  const open = (task: TGA3TaskListItem) => navigate(`/tasks/${encodeURIComponent(task.id)}/runtime`);
  async function confirmDelete() {
    if (!removeTask || deleting) return;
    setDeleting(true); setMessage("");
    try {
      await deleteTask(removeTask.id);
      setRemoveTask(null);
      setMessage(`任务“${removeTask.title}”已彻底删除。`);
      await tasks.refetch();
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "删除任务失败"); }
    finally { setDeleting(false); }
  }
  const columns: Array<Column<TGA3TaskListItem>> = [
    { id: "name", header: "任务名称", render: (task) => <span className="task-name-cell"><strong>{task.title}</strong><small>#{task.id}</small></span> },
    { id: "scene", header: "场景", render: (task) => <span className="cell-muted">{sceneLabel(task.scene_id)}</span> },
    { id: "state", header: "状态", render: (task) => <span className={`ref-chip ${STATE_TONES[task.state] ?? "tone-muted"}`}>{statusLabel(task.state)}</span> },
    { id: "blackboard", header: "黑板序号", render: (task) => task.blackboard_seq ?? 0, align: "center" },
    { id: "dialogue", header: "对话序号", render: (task) => task.dialogue_seq ?? 0, align: "center" },
    { id: "updated", header: "更新时间", render: (task) => <span className="cell-muted">{formatDate(task.updated_at)}</span> },
    { id: "actions", header: "操作", width: "70px", align: "center", render: (task) => <button className="task-delete-button" aria-label={`删除任务 ${task.title}`} title="删除任务" onKeyDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setRemoveTask(task); }}><Trash2 size={15} /></button> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head"><div><h1>任务</h1><p>直接展示 TGA3 任务状态、黑板和对话进度。</p></div><button className="ref-primary-button" onClick={() => navigate("/tasks/new")}><Plus size={16} />创建任务</button></header>
    {message ? <p className="settings-message" role="status">{message}</p> : null}
    <label className="ref-search is-wide"><Search size={16} /><input aria-label="搜索任务" placeholder="搜索任务名称或 ID" value={query} onChange={(event) => update("query", event.target.value)} /></label>
    <section className="ref-filter-row"><select aria-label="场景筛选" value={scene} onChange={(event) => update("scene", event.target.value)}><option value="">所有场景</option>{TASK_MODES.map((value) => <option key={value} value={value}>{sceneLabel(value)}</option>)}</select><select aria-label="状态筛选" value={state} onChange={(event) => update("state", event.target.value)}><option value="">所有状态</option>{STATES.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select><div className="view-toggle push-end"><button className={view === "list" ? "active" : ""} aria-label="列表视图" onClick={() => update("view", "")}><List size={15} /></button><button className={view === "cards" ? "active" : ""} aria-label="卡片视图" onClick={() => update("view", "cards")}><Grid2X2 size={15} /></button></div></section>
    {tasks.isLoading ? <LoadingSkeleton label="正在读取任务" rows={6} /> : tasks.isError ? <ErrorState description={tasks.error instanceof Error ? tasks.error.message : "无法读取任务"} actionLabel="重试" onAction={() => void tasks.refetch()} /> : <>{view === "cards" ? visible.length ? <div className="task-card-grid ref-fill">{visible.map((task) => <article key={task.id} className="task-ref-card"><header><span className={`ref-chip ${STATE_TONES[task.state] ?? "tone-muted"}`}>{statusLabel(task.state)}</span><span className="ref-chip tone-muted">{sceneLabel(task.scene_id)}</span><button className="task-delete-button" aria-label={`删除任务 ${task.title}`} title="删除任务" onClick={() => setRemoveTask(task)}><Trash2 size={15} /></button></header><button className="task-ref-card-main" onClick={() => open(task)}><h3>{task.title}</h3><p>#{task.id}</p></button><div className="task-ref-card-stats"><span><b>{task.blackboard_seq ?? 0}</b>黑板序号</span><span><b>{task.dialogue_seq ?? 0}</b>对话序号</span><span><b>{formatDate(task.updated_at)}</b>最近更新</span></div></article>)}</div> : <EmptyState label="没有匹配的任务" /> : <CatalogTable fill label="任务列表" columns={columns} rows={visible} rowKey={(task) => task.id} onSelect={open} emptyLabel="没有匹配的任务" />}<Pagination total={rows.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} /></>}
    <ConfirmDialog open={Boolean(removeTask)} title={`删除任务 ${removeTask?.title ?? ""}`} description="将停止仍在运行的 Agent，并永久删除该任务的数据库记录、工作区、输入、产物和报告。" confirmLabel="彻底删除" danger busy={deleting} onCancel={() => setRemoveTask(null)} onConfirm={() => void confirmDelete()} />
  </div>;
}

function sceneLabel(scene: string) { return MODE_PROFILES[scene as keyof typeof MODE_PROFILES]?.label ?? scene; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN"); }
