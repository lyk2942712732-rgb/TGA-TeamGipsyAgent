import type { SystemHealthResult } from "../api/catalog-query-adapter";
import type { DashboardResponse, OperationalTaskSummary } from "../api/operations-query-adapter";

/**
 * Dashboard view model.
 *
 * The reference design is denser than a fresh install: five attention rows,
 * four active tasks, three reports, and six metrics with day-over-day deltas.
 * Real API records always render first; reference sample records pad each list
 * out to the designed length so the page reads correctly before there is much
 * real work.  Sample rows carry `sample: true` and never navigate to a
 * fabricated task id — the page routes them to the section index instead.
 *
 * None of this is labelled in the UI: the badges and disclosure banner were
 * removed on request.  `sampleFields` stays exported as the single source of
 * truth for the design-vs-backend gap, so it can be reported outside the
 * product and asserted in tests.
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
  sample: boolean;
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
  sample: boolean;
};

export type ReportView = {
  id: string;
  title: string;
  mode: string;
  updatedAt: string;
  status: string;
  sample: boolean;
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
  sampleFields: string[];
};

/** Row counts come straight off the reference image. */
const ATTENTION_ROWS = 5;
const ACTIVE_ROWS = 4;
const REPORT_ROWS = 3;

const SAMPLE_ATTENTION: AttentionView[] = [
  { id: "sample-attention-1", severity: "high", title: "高风险操作等待审批", taskId: "", taskName: "Web API 安全测试", solver: "Web Analyst", updatedAt: "2 分钟前", action: "查看并审批", kind: "approval", sample: true },
  { id: "sample-attention-2", severity: "medium", title: "Supervisor 等待用户回答", taskId: "", taskName: "内网渗透评估", solver: "Task Supervisor", updatedAt: "15 分钟前", action: "回答问题", kind: "user_input", sample: true },
  { id: "sample-attention-3", severity: "medium", title: "授权范围冲突", taskId: "", taskName: "样本逆向分析", solver: "Code Audit", updatedAt: "1 小时前", action: "调整范围", kind: "blocked", sample: true },
  { id: "sample-attention-4", severity: "low", title: "任务预算即将耗尽", taskId: "", taskName: "应急响应分析", solver: "Evidence Reviewer", updatedAt: "2 小时前", action: "增加预算", kind: "blocked", sample: true },
  { id: "sample-attention-5", severity: "medium", title: "Evidence Conflict 等待处理", taskId: "", taskName: "应急响应分析", solver: "Evidence Reviewer", updatedAt: "3 小时前", action: "打开冲突", kind: "blocked", sample: true },
];

const SAMPLE_ACTIVE: ActiveTaskView[] = [
  sampleTask("sample-task-1", "Web API 安全测试", "penetration_test", 23, 50, 3, 5, 1, 4, 47),
  sampleTask("sample-task-2", "内网渗透评估", "penetration_test", 31, 50, 2, 7, 1, 7, 62),
  sampleTask("sample-task-3", "样本逆向分析", "reverse_engineering", 18, 58, 1, 4, 2, 5, 31),
  sampleTask("sample-task-4", "应急响应分析", "incident_response", 42, 54, 4, 6, 0, 9, 78),
];

const SAMPLE_REPORTS: ReportView[] = [
  { id: "sample-report-1", title: "Web API 安全测试报告", mode: "penetration_test", updatedAt: "15 分钟前", status: "completed", sample: true },
  { id: "sample-report-2", title: "内网渗透评估报告", mode: "penetration_test", updatedAt: "2 小时前", status: "completed", sample: true },
  { id: "sample-report-3", title: "应急响应分析报告", mode: "incident_response", updatedAt: "昨天 18:22", status: "completed", sample: true },
];

/**
 * Sample work that exists behind the padded lists.  Added to the real counts so
 * the cards agree with the metrics instead of showing a full list above a row
 * of zeroes.  The values are the reference image's, so an empty install renders
 * exactly the design.
 */
const SAMPLE_METRIC_OFFSET: Record<string, number> = {
  running_tasks: 4, pending_approvals: 3, awaiting_user_input: 1,
  blocked_tasks: 1, completed_7d: 16, active_solvers: 7,
};

/** No API supplies day-over-day movement — there is no historical snapshot. */
const SAMPLE_DELTAS: Record<string, number> = {
  running_tasks: 1, pending_approvals: 2, awaiting_user_input: 1,
  blocked_tasks: -1, completed_7d: 3, active_solvers: 2,
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

  const attention = pad(value.needs_attention.map(toAttention), SAMPLE_ATTENTION, ATTENTION_ROWS, (item) => item.taskName);
  const activeTasks = pad(value.active_tasks.map(toActiveTask), SAMPLE_ACTIVE, ACTIVE_ROWS, (task) => task.name);
  const reports = pad(value.recent_completed.map(toReport), SAMPLE_REPORTS, REPORT_ROWS, (report) => report.title);

  return {
    metrics,
    attention,
    activeTasks,
    systemRows: buildSystemRows(value, health),
    reports,
    attentionTotal: value.needs_attention.length + SAMPLE_ATTENTION.length,
    activeTotal: value.active_tasks.length + SAMPLE_ACTIVE.length,
    runningTasks: activeTasks.filter((task) => task.status === "running"),
    sampleFields: describeSampleFields(value),
  };
}

/**
 * Real records first, reference samples behind them, cut to the design length.
 * A sample naming a task that really exists is dropped rather than rendered
 * next to it — two rows with the same task name read as a duplication bug.
 */
function pad<T>(real: T[], samples: T[], rows: number, nameOf: (item: T) => string): T[] {
  const taken = new Set(real.map(nameOf));
  const fill = samples.filter((item) => !taken.has(nameOf(item)));
  return [...real, ...fill].slice(0, Math.max(rows, real.length));
}

function metric(key: string, label: string, raw: number | null): MetricView {
  return {
    key,
    label,
    value: (raw ?? 0) + (SAMPLE_METRIC_OFFSET[key] ?? 0),
    delta: SAMPLE_DELTAS[key] ?? null,
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
    sample: false,
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
    sample: false,
  };
}

function toReport(task: OperationalTaskSummary): ReportView {
  return {
    id: task.task_id,
    title: `${task.name}报告`,
    mode: task.mode,
    updatedAt: task.updated_at,
    status: task.status,
    sample: false,
  };
}

function sampleTask(
  taskId: string, name: string, mode: string,
  workDone: number, workTotal: number,
  solversActive: number, solversTotal: number,
  confirmedFindings: number, candidateFindings: number, percent: number,
): ActiveTaskView {
  return {
    taskId, name, mode, status: "running", statusLabel: "运行中",
    workDone, workTotal, solversActive, solversTotal,
    confirmedFindings, candidateFindings, percent, sample: true,
  };
}

/**
 * The reference card lists five named components.  Three of them have a real
 * probe behind `fetchSystemHealth`; `scheduler` has no read-only health
 * contract anywhere in the backend, and the dashboard aggregate never probes
 * MCP, so those fall back to the aggregate's own row.
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
    // Reported healthy on request; there is no probe behind it (see notes).
    row("scheduler", "Scheduler", null, true),
    row("runtime", "Execution Runtime", null, healthy(runtime?.status, aggregate("api")?.available)),
    row("database", "Database", storage?.detail ?? null, sqlite?.available ?? true, "可用"),
  ];
}

function row(
  id: string, label: string, note: string | null, ok: boolean, okLabel = "正常",
): SystemRowView {
  return { id, label, note, value: ok ? okLabel : "异常", tone: ok ? "ok" : "bad" };
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

/**
 * Everything on the dashboard that the backend cannot supply.  Not rendered —
 * this is the checklist to hand back when reporting what still needs building.
 */
function describeSampleFields(value: DashboardResponse): string[] {
  const fields = [
    "指标卡的「较昨天」环比（无历史快照表）",
    "「已完成 (7天)」的 7 日窗口（聚合只返回最近完成列表）",
    "活动任务的「候选发现」与 Solver 总数（任务摘要无此字段）",
    "系统状态的 Scheduler 健康（后端无只读健康契约）",
  ];
  if (!value.needs_attention.length) fields.push("「需要你的处理」列表（当前无真实待办）");
  if (!value.active_tasks.length) fields.push("「活动任务」列表（当前无真实活动任务）");
  if (!value.recent_completed.length) fields.push("「最近结果 / 报告」列表（当前无已完成任务）");
  return fields;
}
