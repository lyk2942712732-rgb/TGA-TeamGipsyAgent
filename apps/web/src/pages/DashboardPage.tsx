import {
  Bot, ChevronRight, CircleAlert, CircleCheck, CircleDot, CircleHelp, CirclePlay,
  Clock, Cpu, Database, FileText, Layers, Timer,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SystemHealthResult } from "../api/catalog-query-adapter";
import type { DashboardResponse } from "../api/operations-query-adapter";
import {
  buildDashboardView, SEVERITY_LABELS,
  type ActiveTaskView, type AttentionView, type MetricView, type ReportView, type SystemRowView,
} from "./dashboard-view";
import { MODE_PROFILES } from "../modes";

const METRIC_ICONS: Record<string, { icon: LucideIcon; tone: string }> = {
  running_tasks: { icon: CirclePlay, tone: "info" },
  pending_approvals: { icon: Timer, tone: "warning" },
  awaiting_user_input: { icon: CircleHelp, tone: "info" },
  blocked_tasks: { icon: CircleAlert, tone: "danger" },
  completed_7d: { icon: CircleCheck, tone: "success" },
  active_solvers: { icon: Bot, tone: "violet" },
};

const SYSTEM_ICONS: Record<string, LucideIcon> = {
  models: Layers, mcp: CircleDot, scheduler: Clock, runtime: Cpu, database: Database,
};

export function DashboardPage({ value, health, onNew, onTask, onTasks, onRuntime, onApprovals, onSystem, onReports }: {
  value: DashboardResponse;
  health?: SystemHealthResult;
  onNew: () => void;
  onTask: (taskId: string) => void;
  onTasks: () => void;
  onRuntime: (taskId: string) => void;
  onApprovals: (taskId?: string) => void;
  onSystem: () => void;
  onReports: () => void;
}) {
  const view = buildDashboardView(value, health);

  return <div className="ref-page dashboard-ref">
    <header className="dashboard-greeting">
      <div>
        <h1>欢迎回来，Admin</h1>
        <p>今天是 {formatToday(value.generated_at)}，祝你工作顺利</p>
      </div>
      <button className="ref-primary-button" onClick={onNew}>创建任务</button>
    </header>

    <section className="dashboard-metrics" aria-label="运行指标">
      {view.metrics.map((metric) => <MetricCard key={metric.key} metric={metric} />)}
    </section>

    <div className="dashboard-columns">
      <section className="ref-card">
        <header className="ref-card-head"><h2>需要你的处理</h2></header>
        <ul className="attention-items">
          {view.attention.map((item) => <AttentionRow
            key={item.id}
            item={item}
            onApprovals={onApprovals}
            onTask={onTask}
            onTasks={onTasks}
          />)}
        </ul>
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={() => onApprovals()}>查看全部（{view.attentionTotal}）<ChevronRight size={14} /></button>
        </footer>
      </section>

      <section className="ref-card">
        <header className="ref-card-head"><h2>活动任务</h2></header>
        <ul className="active-task-items">
          {view.activeTasks.map((task) => <ActiveTaskRow
            key={task.taskId}
            task={task}
            onOpen={onRuntime}
            onTasks={onTasks}
          />)}
        </ul>
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={onTasks}>查看全部任务（{view.activeTotal}）<ChevronRight size={14} /></button>
        </footer>
      </section>
    </div>

    <div className="dashboard-columns">
      <section className="ref-card">
        <header className="ref-card-head"><h2>系统状态</h2></header>
        <ul className="system-rows">
          {view.systemRows.map((item) => <SystemRow key={item.id} item={item} />)}
        </ul>
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={onSystem}>查看系统状态 <ChevronRight size={14} /></button>
        </footer>
      </section>

      <section className="ref-card">
        <header className="ref-card-head"><h2>最近结果 / 报告</h2></header>
        <ul className="report-rows">
          {view.reports.map((report) => <ReportRow
            key={report.id}
            report={report}
            onOpen={onTask}
            onReports={onReports}
          />)}
        </ul>
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={onReports}>查看全部报告 <ChevronRight size={14} /></button>
        </footer>
      </section>
    </div>
  </div>;
}

function MetricCard({ metric }: { metric: MetricView }) {
  const meta = METRIC_ICONS[metric.key] ?? { icon: CirclePlay, tone: "info" };
  return <article className={`dashboard-metric tone-${meta.tone}`}>
    <header>
      <span className="metric-icon" aria-hidden="true"><meta.icon size={17} /></span>
      <span className="metric-label">{metric.label}</span>
    </header>
    <strong>{metric.value}</strong>
    <footer>
      较昨天 <b className={metric.delta !== null && metric.delta < 0 ? "delta-down" : "delta-up"}>
        {metric.delta === null ? "—" : metric.delta > 0 ? `+${metric.delta}` : metric.delta}
      </b>
    </footer>
  </article>;
}

function AttentionRow({ item, onApprovals, onTask, onTasks }: {
  item: AttentionView;
  onApprovals: (taskId?: string) => void;
  onTask: (taskId: string) => void;
  onTasks: () => void;
}) {
  // Sample rows carry no real task id, so they open the section index rather
  // than routing to a task that does not exist.
  const open = () => {
    if (item.sample) return item.kind === "approval" ? onApprovals() : onTasks();
    return item.kind === "approval" ? onApprovals(item.taskId) : onTask(item.taskId);
  };
  return <li>
    <span className={`severity-chip tone-${item.severity}`}>{SEVERITY_LABELS[item.severity]}</span>
    <div className="attention-copy">
      <strong>{item.title}</strong>
      <small>任务: {item.taskName} · Solver: {item.solver}</small>
    </div>
    <time dateTime={item.sample ? undefined : item.updatedAt}>{item.sample ? item.updatedAt : relativeTime(item.updatedAt)}</time>
    <button className="ref-secondary-button" onClick={open}>{item.action}</button>
  </li>;
}

function ActiveTaskRow({ task, onOpen, onTasks }: {
  task: ActiveTaskView;
  onOpen: (taskId: string) => void;
  onTasks: () => void;
}) {
  return <li>
    <button className="active-task-row" onClick={() => task.sample ? onTasks() : onOpen(task.taskId)}>
      <span className="active-task-icon" aria-hidden="true"><Bot size={16} /></span>
      <span className="active-task-name">
        <strong>{task.name}</strong>
        <small>模式: {modeLabel(task.mode)} · <em className={`status-chip tone-${task.status}`}>{task.statusLabel}</em></small>
      </span>
      <span className="active-task-stats">
        <Stat label="工作项" value={`${task.workDone}/${task.workTotal || "-"}`} />
        <Stat label="运行 Solver" value={task.solversTotal === null ? String(task.solversActive) : `${task.solversActive}/${task.solversTotal}`} />
        <Stat label="确认发现" value={String(task.confirmedFindings)} />
        <Stat label="候选发现" value={task.candidateFindings === null ? "—" : String(task.candidateFindings)} />
      </span>
      <span className="active-task-progress">
        <b>{task.percent}%</b>
        <i><em style={{ width: `${task.percent}%` }} /></i>
      </span>
    </button>
  </li>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <span className="active-task-stat">
    <small>{label}</small>
    <b>{value}</b>
  </span>;
}

function SystemRow({ item }: { item: SystemRowView }) {
  const Icon = SYSTEM_ICONS[item.id] ?? Database;
  return <li>
    <span className="system-row-icon" aria-hidden="true"><Icon size={16} /></span>
    <strong>{item.label}</strong>
    <span className="system-row-status">
      {item.note ? <small className="system-row-note">{item.note}</small> : null}
      <em className={`system-row-value tone-${item.tone}`}>{item.value}</em>
    </span>
  </li>;
}

function ReportRow({ report, onOpen, onReports }: {
  report: ReportView;
  onOpen: (taskId: string) => void;
  onReports: () => void;
}) {
  return <li>
    <span className="report-icon" aria-hidden="true"><FileText size={16} /></span>
    <button className="report-main" onClick={() => report.sample ? onReports() : onOpen(report.id)}>
      <strong>{report.title}</strong>
    </button>
    <span className="chip tone-neutral">{modeLabel(report.mode)}</span>
    <time>{report.sample ? report.updatedAt : relativeTime(report.updatedAt)}</time>
    <em className="report-status">完成</em>
  </li>;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}

function formatToday(value: string): string {
  const date = value ? new Date(value) : new Date();
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).replace(/\//g, "-");
}

function relativeTime(value: string): string {
  if (!value) return "";
  const elapsed = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(elapsed) || elapsed < 0) return "刚刚";
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}
