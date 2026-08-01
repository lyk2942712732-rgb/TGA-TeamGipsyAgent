import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Grid2X2, List, Plus, Search, SlidersHorizontal } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { fetchTaskList } from "../api/task-query-adapter";
import type { TaskListItem } from "../api/tasks";
import { CatalogTable, Pagination, usePage, type Column } from "../components/ui/CatalogTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { statusLabel } from "../shared/status";
import { MODE_PROFILES, TASK_MODES } from "../modes";

/**
 * 任务 (reference image 02).
 *
 * `/api/v2/tasks` supplies every column except 严重度 — the task summary has no
 * aggregated finding severity, so rows show a dash there.  Every row comes from
 * the API; an install with no schema-v6 task renders the empty state.
 */

const STATUSES = ["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"];

type TaskRow = {
  taskId: string;
  displayId: string;
  name: string;
  mode: string;
  status: string;
  percent: number;
  solversActive: number;
  solversTotal: number | null;
  approvals: number;
  severity: "高" | "中" | "低" | null;
  updatedAt: string;
};

const STATUS_TONES: Record<string, string> = {
  running: "tone-ok", completed: "tone-ok",
  awaiting_approval: "tone-warn", paused: "tone-warn", created: "tone-muted", queued: "tone-muted",
  blocked: "tone-danger", failed: "tone-danger", cancelled: "tone-muted",
};
const SEVERITY_TONES: Record<string, string> = { 高: "tone-danger", 中: "tone-warn", 低: "tone-ok" };

export function TaskListPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = params.get("query") ?? "";
  const mode = params.get("mode") ?? "";
  const status = params.get("status") ?? "";
  const attention = params.get("needs_attention");
  const view = params.get("view") === "cards" ? "cards" : "list";

  const filters = useMemo(() => ({
    query,
    mode: mode || undefined,
    status: status || undefined,
    needsAttention: attention === null ? undefined : attention === "true",
    offset: 0,
    limit: 100,
  }), [query, mode, status, attention]);

  const tasks = useQuery({ queryKey: ["task-list", filters], queryFn: () => fetchTaskList(filters) });

  const rows = useMemo(() => (tasks.data?.tasks ?? []).map(toRow), [tasks.data]);

  const visible = usePage(rows, pageSize, page);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
    setPage(1);
  };

  const open = (row: TaskRow) => navigate(`/tasks/${encodeURIComponent(row.taskId)}`);

  const columns: Array<Column<TaskRow>> = [
    {
      id: "name", header: "任务名称",
      render: (row) => <span className="task-name-cell">
        <strong>{row.name}</strong>
        <small>#{row.displayId}</small>
      </span>,
    },
    { id: "mode", header: "模式", render: (row) => <span className="cell-muted">{modeLabel(row.mode)}</span> },
    { id: "status", header: "状态", render: (row) => <span className={`ref-chip ${STATUS_TONES[row.status] ?? "tone-muted"}`}>{statusLabel(row.status)}</span> },
    {
      id: "progress", header: "进度",
      render: (row) => <div className="task-progress">
        <span>{row.percent}%</span>
        <i><em style={{ width: `${row.percent}%` }} /></i>
      </div>,
    },
    {
      id: "solvers", header: "Solver",
      render: (row) => row.solversTotal === null ? String(row.solversActive) : `${row.solversActive}/${row.solversTotal}`,
      align: "center",
    },
    { id: "approvals", header: "审批", render: (row) => row.approvals, align: "center" },
    {
      id: "severity", header: "严重度",
      render: (row) => row.severity
        ? <span className={`ref-chip ${SEVERITY_TONES[row.severity]}`}>{row.severity}</span>
        : <span className="field-empty">—</span>,
    },
    { id: "updated", header: "更新时间 ↓", render: (row) => <span className="cell-muted">{row.updatedAt}</span> },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>任务</h1>
        <p>管理和查看所有任务</p>
      </div>
      <button className="ref-primary-button" onClick={() => navigate("/tasks/new")}><Plus size={16} />创建任务</button>
    </header>

    <label className="ref-search is-wide">
      <Search size={16} aria-hidden="true" />
      <input
        aria-label="搜索任务"
        placeholder="搜索任务名称/ID/标签..."
        value={query}
        onChange={(event) => update("query", event.target.value)}
      />
    </label>

    <section className="ref-filter-row" aria-label="筛选任务">
      <select aria-label="模式筛选" value={mode} onChange={(event) => update("mode", event.target.value)}>
        <option value="">所有模式</option>
        {TASK_MODES.map((value) => <option key={value} value={value}>{modeLabel(value)}</option>)}
      </select>
      <select aria-label="状态筛选" value={status} onChange={(event) => update("status", event.target.value)}>
        <option value="">所有状态</option>
        {STATUSES.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}
      </select>
      <select aria-label="处理状态筛选" value={attention ?? ""} onChange={(event) => update("needs_attention", event.target.value)}>
        <option value="">需要我处理</option>
        <option value="true">仅需要处理</option>
        <option value="false">无需处理</option>
      </select>
      <button className="ref-filter-button" onClick={() => toast.notifyUnavailable("更多筛选")}>
        <SlidersHorizontal size={15} />更多筛选
      </button>
      <div className="view-toggle push-end" role="group" aria-label="视图切换">
        <button className={view === "list" ? "active" : ""} aria-pressed={view === "list"}
          aria-label="列表视图" onClick={() => update("view", "")}><List size={15} /></button>
        <button className={view === "cards" ? "active" : ""} aria-pressed={view === "cards"}
          aria-label="卡片视图" onClick={() => update("view", "cards")}><Grid2X2 size={15} /></button>
      </div>
    </section>

    {tasks.isLoading ? <LoadingSkeleton label="正在读取任务列表" rows={6} />
      : tasks.isError ? <ErrorState
        description={tasks.error instanceof Error ? tasks.error.message : "无法读取任务列表"}
        actionLabel="重试"
        onAction={() => void tasks.refetch()}
      />
      : <>
        {view === "cards"
          ? visible.length
            ? <div className="task-card-grid ref-fill">
              {visible.map((row) => <TaskCard key={row.taskId} task={row} onOpen={() => open(row)} />)}
            </div>
            : <EmptyState label="没有匹配的任务" />
          : <CatalogTable
            fill
            label="任务列表"
            columns={columns}
            rows={visible}
            rowKey={(row) => row.taskId}
            onSelect={open}
            emptyLabel="没有匹配的任务"
          />}
        <Pagination total={rows.length} pageSize={pageSize} page={page} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />
      </>}
  </div>;
}

function TaskCard({ task, onOpen }: { task: TaskRow; onOpen: () => void }) {
  return <article className="task-ref-card">
    <header>
      <span className={`ref-chip ${STATUS_TONES[task.status] ?? "tone-muted"}`}>{statusLabel(task.status)}</span>
      <span className="ref-chip tone-muted">{modeLabel(task.mode)}</span>
    </header>
    <button className="task-ref-card-main" onClick={onOpen}>
      <h3>{task.name}</h3>
      <p>#{task.displayId}</p>
    </button>
    <div className="task-ref-card-stats">
      <span><b>{task.solversTotal === null ? task.solversActive : `${task.solversActive}/${task.solversTotal}`}</b>Solver</span>
      <span><b>{task.approvals}</b>审批</span>
      <span><b>{task.severity ?? "—"}</b>严重度</span>
      <span><b>{task.percent}%</b>进度</span>
    </div>
    <div className="task-progress"><span>{task.percent}%</span><i><em style={{ width: `${task.percent}%` }} /></i></div>
  </article>;
}

function toRow(task: TaskListItem): TaskRow {
  const total = task.intent_total ?? 0;
  const done = task.intent_completed ?? 0;
  return {
    taskId: task.task_id,
    displayId: task.task_id,
    name: task.name,
    mode: task.mode,
    status: task.status,
    percent: total ? Math.round(done / total * 100) : 0,
    solversActive: task.active_solvers ?? 0,
    // The list projection has no team size and no aggregated finding severity.
    solversTotal: null,
    approvals: task.pending_approvals ?? 0,
    severity: null,
    updatedAt: formatDate(task.updated_at ?? task.created_at),
  };
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}

function formatDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  if (minutes < 2880) return "昨天";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
