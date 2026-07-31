import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Grid2X2, List, Plus, Search } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { fetchTaskList, taskListQueryString } from "../api/task-query-adapter";
import type { TaskListItem } from "../api/tasks";
import { DataTable } from "../components/ui/DataTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { FilterBar } from "../components/ui/FilterBar";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../shared/StatusBadge";
import { statusLabel } from "../shared/status";
import { MODE_PROFILES, TASK_MODES } from "../modes";

const STATUSES = ["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"];

export function TaskListPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get("query") ?? "";
  const mode = params.get("mode") ?? "";
  const status = params.get("status") ?? "";
  const attention = params.get("needs_attention");
  const view = params.get("view") === "cards" ? "cards" : "list";
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);
  const filters = useMemo(() => ({ query, mode: mode || undefined, status: status || undefined, needsAttention: attention === null ? undefined : attention === "true", offset, limit: 100 }), [query, mode, status, attention, offset]);
  const tasks = useQuery({ queryKey: ["task-list", filters], queryFn: () => fetchTaskList(filters) });

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "offset" && key !== "view") next.delete("offset");
    setParams(next);
  };
  const clear = () => setParams(view === "cards" ? new URLSearchParams("view=cards") : new URLSearchParams());
  const rows = tasks.data?.tasks ?? [];

  return <section className="page-stack task-list-page">
    <PageHeader
      eyebrow="WORKSPACE / TASKS"
      title="任务"
      description="按生命周期浏览任务，先查看详情，再进入实时运行或只读回放。"
      breadcrumbs={[{ label: "TGA", href: "/" }, { label: "任务" }]}
      actions={<button className="primary-action" onClick={() => navigate("/tasks/new")}><Plus size={16} />创建任务</button>}
    />
    <FilterBar resultCount={tasks.data?.total ?? rows.length} actions={<>
      <button className={view === "list" ? "view-toggle active" : "view-toggle"} aria-label="列表视图" aria-pressed={view === "list"} onClick={() => update("view", "list")}><List size={15} /></button>
      <button className={view === "cards" ? "view-toggle active" : "view-toggle"} aria-label="卡片视图" aria-pressed={view === "cards"} onClick={() => update("view", "cards")}><Grid2X2 size={15} /></button>
    </>}>
      <label className="task-search-field"><Search size={14} aria-hidden="true" /><span className="sr-only">搜索任务</span><input aria-label="搜索任务" value={query} placeholder="搜索名称或 Task ID" onChange={(event) => update("query", event.target.value)} /></label>
      <label>TaskMode<select aria-label="TaskMode" value={mode} onChange={(event) => update("mode", event.target.value)}><option value="">全部模式</option>{TASK_MODES.map((item) => <option key={item} value={item}>{MODE_PROFILES[item].label}</option>)}</select></label>
      <label>状态<select aria-label="任务状态" value={status} onChange={(event) => update("status", event.target.value)}><option value="">全部状态</option>{STATUSES.map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select></label>
      <label>处理状态<select aria-label="需要处理" value={attention ?? ""} onChange={(event) => update("needs_attention", event.target.value)}><option value="">全部任务</option><option value="true">需要处理</option><option value="false">无需处理</option></select></label>
      {(query || mode || status || attention !== null) ? <button className="text-button" onClick={clear}>清除筛选</button> : null}
    </FilterBar>
    {tasks.isLoading ? <LoadingSkeleton label="正在读取任务列表" rows={7} /> : null}
    {tasks.isError ? <ErrorState title="任务列表加载失败" description={tasks.error instanceof Error ? tasks.error.message : "无法读取任务列表"} actionLabel="重试" onAction={() => void tasks.refetch()} /> : null}
    {!tasks.isLoading && !tasks.isError && !rows.length ? <EmptyState title={query || mode || status || attention !== null ? "没有匹配的任务" : "还没有任务"} description={query || mode || status || attention !== null ? "尝试清除筛选条件，或创建新的任务。" : "创建第一个任务后，它会出现在这里。"} action={<button className="primary-action" onClick={() => navigate("/tasks/new")}><Plus size={15} />创建第一个任务</button>} /> : null}
    {!tasks.isLoading && !tasks.isError && rows.length ? view === "list" ? <TaskTable rows={rows} onOpen={(id) => navigate(`/tasks/${encodeURIComponent(id)}`)} onRuntime={(id) => navigate(`/tasks/${encodeURIComponent(id)}/runtime`)} onReplay={(id) => navigate(`/tasks/${encodeURIComponent(id)}/replay`)} /> : <TaskCardGrid rows={rows} onOpen={(id) => navigate(`/tasks/${encodeURIComponent(id)}`)} onRuntime={(id) => navigate(`/tasks/${encodeURIComponent(id)}/runtime`)} onReplay={(id) => navigate(`/tasks/${encodeURIComponent(id)}/replay`)} /> : null}
    {!tasks.isLoading && !tasks.isError && (offset > 0 || tasks.data?.next_offset != null) ? <nav className="task-pagination" aria-label="任务分页"><button disabled={offset === 0} onClick={() => update("offset", String(Math.max(0, offset - 100)))}>上一页</button><span>第 {Math.floor(offset / 100) + 1} 页</span><button disabled={tasks.data?.next_offset == null} onClick={() => update("offset", String(tasks.data?.next_offset ?? offset))}>下一页</button></nav> : null}
  </section>;
}

function TaskTable({ rows, onOpen, onRuntime, onReplay }: { rows: TaskListItem[]; onOpen: (id: string) => void; onRuntime: (id: string) => void; onReplay: (id: string) => void }) {
  return <DataTable label="任务列表" rows={rows} rowKey={(row) => row.task_id} onRowClick={(row) => onOpen(row.task_id)} columns={[
    { id: "name", header: "任务", render: (row) => <div className="task-cell"><strong>{row.name || row.task_id}</strong><code>{row.task_id}</code></div> },
    { id: "mode", header: "模式", render: (row) => MODE_PROFILES[row.mode]?.label ?? row.mode },
    { id: "status", header: "状态", render: (row) => <StatusBadge value={row.status} /> },
    { id: "progress", header: "Intent", render: (row) => <span>{row.intent_completed ?? 0} / {row.intent_total ?? "-"}</span> },
    { id: "solver", header: "Solver", render: (row) => row.active_solvers ?? "-" },
    { id: "approval", header: "待处理", render: (row) => row.needs_attention ? <span className="attention-mark"><Check size={13} />{row.pending_approvals ? `${row.pending_approvals} 项审批` : "需要处理"}</span> : "-" },
    { id: "updated", header: "更新时间", render: (row) => formatDate(row.updated_at || row.created_at) },
    { id: "actions", header: "操作", render: (row) => <div className="task-row-actions"><button onClick={(event) => { event.stopPropagation(); onOpen(row.task_id); }}>打开</button><button onClick={(event) => { event.stopPropagation(); onRuntime(row.task_id); }}>运行</button><button onClick={(event) => { event.stopPropagation(); onReplay(row.task_id); }}>回放</button></div> },
  ]} />;
}

function TaskCardGrid({ rows, onOpen, onRuntime, onReplay }: { rows: TaskListItem[]; onOpen: (id: string) => void; onRuntime: (id: string) => void; onReplay: (id: string) => void }) {
  return <div className="task-list-card-grid">{rows.map((row) => <article className="task-list-card" key={row.task_id}>
    <header><div><StatusBadge value={row.status} /><code>{row.task_id}</code></div><span>{MODE_PROFILES[row.mode]?.label ?? row.mode}</span></header>
    <button className="task-list-card-main" onClick={() => onOpen(row.task_id)}><h2>{row.name || row.task_id}</h2><p>{row.target_summary || "本地输入任务"}</p><div><span>Intent <b>{row.intent_completed ?? 0}/{row.intent_total ?? "-"}</b></span><span>Solver <b>{row.active_solvers ?? "-"}</b></span><span>结果 <b>{row.findings ?? 0}</b></span></div></button>
    <footer><small>{formatDate(row.updated_at || row.created_at)}</small><div><button onClick={() => onRuntime(row.task_id)}>进入运行</button><button onClick={() => onReplay(row.task_id)}>回放</button></div></footer>
  </article>)}</div>;
}

function formatDate(value?: string) { return value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" }) : "尚未更新"; }
