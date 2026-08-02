import type { SystemHealthResult } from "../api/catalog-query-adapter";
import type { DashboardResponse, OperationalTaskSummary } from "../api/operations-query-adapter";

/**
 * Dashboard view model.
 *
 * Every row and counter here comes from `/api/v2/dashboard`.  The reference
 * design is denser than a fresh install, but padding the lists with invented
 * tasks and approvals made an empty install indistinguishable from a busy one,
 * so the lists now render exactly what the backend reports and fall back to an
 * empty state.
 */

export type MetricView = {
  key: string;
  label: string;
  value: number;
  delta: number | null;
};

export type AttentionView = {
  id: string;
  severity: "high" | "medium" | "low";
  title: string;
  taskId: string;
  taskName: string;
  solver: string;
  updatedAt: string;
  action: string;
  kind: "approval" | "user_input" | "blocked";
};

export type ActiveTaskView = {
  taskId: string;
  name: string;
  mode: string;
  status: string;
  statusLabel: string;
  workDone: number;
  workTotal: number;
  solversActive: number;
  solversTotal: number | null;
  confirmedFindings: number;
  candidateFindings: number | null;
  percent: number;
};

export type ReportView = {
  id: string;
  title: string;
  mode: string;
  updatedAt: string;
  status: string;
};

/** One row of the reference design's five-component system card. */
export type SystemRowView = {
  id: string;
  label: string;
  note: string | null;
  value: string;
  tone: "ok" | "warn" | "bad";
};

export type DashboardView = {
  metrics: MetricView[];
  attention: AttentionView[];
  activeTasks: ActiveTaskView[];
  systemRows: SystemRowView[];
  reports: ReportView[];
  attentionTotal: number;
  activeTotal: number;
  runningTasks: ActiveTaskView[];
};

const STATUS_LABELS: Record<string, string> = {
  running: "运行中", blocked: "已阻塞", paused: "已暂停", queued: "排队中",
  awaiting_input: "待回答", awaiting_user_input: "待回答",
  completed: "已完成", failed: "已失败", cancelled: "已取消",
};

export function buildDashboardView(
  value: DashboardResponse,
  health?: SystemHealthResult,
): DashboardView {
  const metrics: MetricView[] = [
    metric("running_tasks", "运行中任务", value.metrics.running_tasks),
    metric("pending_approvals", "待审批", value.metrics.pending_approvals),
    metric("awaiting_user_input", "待回答", value.metrics.awaiting_user_input),
    metric("blocked_tasks", "阻塞任务", value.metrics.blocked_tasks),
    metric("completed_7d", "已完成 (7天)", value.recent_completed.length),
    metric("active_solvers", "活动 Solver", value.metrics.active_solvers),
  ];

  const attention = value.needs_attention.map(toAttention);
  const activeTasks = value.active_tasks.map(toActiveTask);
  const reports = value.recent_completed.map(toReport);

  return {
    metrics,
    attention,
    activeTasks,
    systemRows: buildSystemRows(value, health),
    reports,
    attentionTotal: value.needs_attention.length,
    activeTotal: value.active_tasks.length,
    runningTasks: activeTasks.filter((task) => task.status === "running"),
  };
}

function metric(key: string, label: string, raw: number | null): MetricView {
  return {
    key,
    label,
    value: raw ?? 0,
    // No API supplies day-over-day movement; there is no historical snapshot.
    delta: null,
  };
}

function toAttention(item: DashboardResponse["needs_attention"][number]): AttentionView {
  return {
    id: item.id,
    severity: severityOf(item.risk, item.kind),
    title: item.title,
    taskId: item.task_id,
    taskName: item.task_name,
    solver: item.description,
    updatedAt: item.updated_at,
    action: ACTION_LABELS[item.kind],
    kind: item.kind,
  };
}

function toActiveTask(task: OperationalTaskSummary): ActiveTaskView {
  const total = task.intent_total ?? 0;
  const done = task.intent_completed ?? 0;
  return {
    taskId: task.task_id,
    name: task.name,
    mode: task.mode,
    status: task.status,
    statusLabel: STATUS_LABELS[task.status] ?? task.status,
    workDone: done,
    workTotal: total,
    solversActive: task.active_solvers,
    // OperationalTaskSummary carries no team size and no candidate-finding count.
    solversTotal: null,
    confirmedFindings: task.findings,
    candidateFindings: null,
    percent: total ? Math.round(done / total * 100) : 0,
  };
}

function toReport(task: OperationalTaskSummary): ReportView {
  return {
    id: task.task_id,
    title: `${task.name}报告`,
    mode: task.mode,
    updatedAt: task.updated_at,
    status: task.status,
  };
}

/**
 * The reference card lists five named components.  Three of them have a real
 * probe behind `fetchSystemHealth`; `scheduler` has no read-only health
 * contract anywhere in the backend, and the dashboard aggregate never probes
 * MCP, so those fall back to the aggregate's own row.  Scheduler reports
 * "未探测" rather than a green "正常" it cannot substantiate.
 */
function buildSystemRows(value: DashboardResponse, health?: SystemHealthResult): SystemRowView[] {
  const probe = (id: string) => health?.components.find((item) => item.id === id);
  const aggregate = (id: string) => value.system_status.find((item) => item.id === id);

  const models = probe("models");
  const mcp = probe("mcp");
  const runtime = probe("runtime");
  const storage = aggregate("task_storage");
  const sqlite = aggregate("sqlite");

  return [
    row("models", "Model Providers", null, healthy(models?.status, aggregate("model")?.available)),
    row("mcp", "MCP Servers", mcp?.detail ?? null, healthy(mcp?.status, true)),
    unprobedRow("scheduler", "Scheduler", aggregate("scheduler")?.detail ?? null),
    row("runtime", "Execution Runtime", null, healthy(runtime?.status, aggregate("api")?.available)),
    row("database", "Database", storage?.detail ?? null, sqlite?.available ?? true, "可用"),
  ];
}

function row(
  id: string, label: string, note: string | null, ok: boolean, okLabel = "正常",
): SystemRowView {
  return { id, label, note, value: ok ? okLabel : "异常", tone: ok ? "ok" : "bad" };
}

/** A component the backend exposes no health contract for. */
function unprobedRow(id: string, label: string, note: string | null): SystemRowView {
  return { id, label, note, value: "未探测", tone: "warn" };
}

function healthy(status: string | undefined, fallback: boolean | undefined): boolean {
  if (status) return status === "healthy" || status === "available";
  return fallback ?? true;
}

const ACTION_LABELS: Record<AttentionView["kind"], string> = {
  approval: "查看并审批",
  user_input: "回答问题",
  blocked: "查看原因",
};

function severityOf(risk: string | null | undefined, kind: AttentionView["kind"]): AttentionView["severity"] {
  if (risk === "destructive") return "high";
  if (risk === "active") return "medium";
  if (risk === "passive") return "low";
  return kind === "approval" ? "high" : kind === "blocked" ? "medium" : "low";
}

export const SEVERITY_LABELS: Record<AttentionView["severity"], string> = {
  high: "高", medium: "中", low: "低",
};
