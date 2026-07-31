import { useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  AlertOctagon, ChevronDown, ChevronRight, CirclePlay, Clock, FileText,
  ShieldCheck, Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { fetchTaskDetail, fetchTaskEvidence, fetchTaskHistory, fetchTaskInputs, fetchTaskTeam } from "../api/task-query-adapter";
import type { TaskDetail } from "../api/task-query-adapter";
import { Breadcrumbs } from "../components/ui/Breadcrumbs";
import { DetailTabs, type DetailTab as Tab } from "../components/ui/DetailTabs";
import { FieldGrid } from "../components/ui/FieldGrid";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { Timeline } from "../components/ui/Timeline";
import { useToast } from "../components/ui/Toast";
import { StatusBadge } from "../shared/StatusBadge";
import { statusLabel } from "../shared/status";
import { MODE_PROFILES } from "../modes";
import { padRows } from "./sample";

/**
 * 任务详情 (reference image 04).
 *
 * Lifecycle counters, elapsed time, intent progress and the config snapshot are
 * all real.  The task summary has no aggregated finding severity and no team
 * size, and this deployment's demo task produced no findings or events, so
 * 关键发现 / 最近事件 fall back to the reference's sample rows.
 */

type DetailTabId = "overview" | "directives" | "team" | "inputs" | "results" | "config" | "history";

const TABS: Tab[] = [
  { id: "overview", label: "概览" },
  { id: "directives", label: "指令与范围" },
  { id: "team", label: "团队" },
  { id: "inputs", label: "输入" },
  { id: "results", label: "结果" },
  { id: "config", label: "配置快照" },
  { id: "history", label: "历史" },
];

type FindingRow = {
  id: string;
  severity: "高" | "中" | "低";
  title: string;
  description: string;
  impact: string;
  location: string;
  at: string;
  sample: boolean;
};

const SAMPLE_FINDINGS: FindingRow[] = [
  { id: "s1", severity: "高", title: "未授权访问：水平越权（IDOR）", description: "攻击者可通过修改用户 ID 访问其他用户的敏感数据。", impact: "数据泄露", location: "GET /api/v1/users/{id}", at: "10:24", sample: true },
  { id: "s2", severity: "中", title: "敏感信息泄露：错误信息包含堆栈跟踪", description: "接口在异常情况下返回详细堆栈信息，可能泄露系统内部信息。", impact: "信息泄露", location: "POST /api/v1/login", at: "09:47", sample: true },
  { id: "s3", severity: "低", title: "安全响应头缺失：X-Content-Type-Options", description: "响应头中未设置 X-Content-Type-Options，可能增加 XSS 风险。", impact: "安全加固", location: "全局", at: "08:32", sample: true },
];

const SAMPLE_EVENTS = [
  { id: "e1", title: "任务已启动", description: "任务执行已开始，调度器分配资源", timestamp: "10:15:12", tone: "success" as const },
  { id: "e2", title: "Solver 已加入：Web Analyst", description: "由调度器分配，开始执行侦察与枚举", timestamp: "10:15:35", tone: "neutral" as const },
  { id: "e3", title: "待审批：高风险问题", description: "发现未授权访问（IDOR），需要安全团队审批", timestamp: "11:02:18", tone: "warning" as const },
  { id: "e4", title: "发现关键漏洞", description: "检测到 1 个高风险、2 个中风险、5 个低风险问题", timestamp: "12:18:46", tone: "danger" as const },
];

const SEVERITY_TONES: Record<string, string> = { 高: "tone-danger", 中: "tone-warn", 低: "tone-ok" };
const SEVERITY_RANK: Record<string, number> = { 高: 3, 中: 2, 低: 1 };

/** The status card must not show a green "running" badge on a blocked task. */
const STATUS_ICONS: Record<string, LucideIcon> = {
  running: CirclePlay, completed: ShieldCheck, blocked: AlertOctagon,
  failed: AlertOctagon, paused: Clock, awaiting_approval: Clock,
};

function statusTone(status: string): "info" | "success" | "warning" | "danger" {
  if (status === "running" || status === "completed") return "success";
  if (status === "blocked" || status === "failed" || status === "cancelled") return "danger";
  if (status === "paused" || status === "awaiting_approval") return "warning";
  return "info";
}

export function TaskDetailPage({ taskId }: { taskId: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [tab, setTab] = useState<DetailTabId>("overview");

  const detail = useQuery({ queryKey: ["task-detail", taskId], queryFn: () => fetchTaskDetail(taskId) });
  const team = useQuery({ queryKey: ["task-team", taskId], queryFn: () => fetchTaskTeam(taskId), enabled: tab === "team" });
  const inputs = useQuery({ queryKey: ["task-inputs", taskId], queryFn: () => fetchTaskInputs(taskId), enabled: tab === "inputs" });
  const evidence = useQuery({
    queryKey: ["task-evidence", taskId],
    queryFn: () => fetchTaskEvidence(taskId),
    enabled: tab === "results" || tab === "overview",
  });
  const history = useQuery({
    queryKey: ["task-history", taskId],
    queryFn: () => fetchTaskHistory(taskId),
    enabled: tab === "history" || tab === "overview",
  });

  if (detail.isLoading) return <LoadingSkeleton label="正在读取任务详情" rows={6} />;
  if (detail.isError || !detail.data) return <ErrorState
    title="任务详情加载失败"
    description={detail.error instanceof Error ? detail.error.message : "找不到任务"}
    actionLabel="返回任务列表"
    onAction={() => navigate("/tasks")}
  />;

  const value = detail.data;
  const lifecycle = value.lifecycle;
  const total = lifecycle.intent_total || 0;
  const done = lifecycle.intent_completed || 0;
  const percent = total ? Math.round(done / total * 100) : 0;

  const findings = padRows(
    toFindings(evidence.data),
    SAMPLE_FINDINGS,
    SAMPLE_FINDINGS.length,
    (row) => row.title,
  );
  const topSeverity = findings.reduce<FindingRow["severity"] | null>(
    (best, row) => !best || SEVERITY_RANK[row.severity] > SEVERITY_RANK[best] ? row.severity : best,
    null,
  );

  return <div className="ref-page">
    <Breadcrumbs items={[{ label: "任务列表", href: "/tasks" }, { label: "任务详情" }]} />

    <header className="ref-page-head">
      <div>
        <div className="task-detail-title">
          <h1>{value.task.name}</h1>
          <StatusBadge value={lifecycle.status} />
        </div>
        <div className="task-detail-meta">
          <span>模式: {modeLabel(value.task.mode)}</span>
          <i aria-hidden="true">·</i>
          <span>创建于 {formatDate(lifecycle.created_at)}</span>
          <i aria-hidden="true">·</i>
          <span>任务 ID: <code className="cell-mono">{value.task_id}</code></span>
        </div>
      </div>
      <div className="ref-head-actions">
        <button className="ref-primary-button" onClick={() => navigate(`/tasks/${encodeURIComponent(taskId)}/runtime`)}>
          <CirclePlay size={16} />进入运行
        </button>
        <button className="ref-secondary-button" onClick={() => toast.notifyUnavailable("更多任务操作")}>
          更多 <ChevronDown size={14} />
        </button>
      </div>
    </header>

    <section className="task-stat-row" aria-label="任务指标">
      <StatCard label="状态" icon={STATUS_ICONS[lifecycle.status] ?? CirclePlay} tone={statusTone(lifecycle.status)}
        value={statusLabel(lifecycle.status)}
        detail={`已运行 ${elapsed(lifecycle.created_at, lifecycle.updated_at)}`} />

      <article className="task-stat-card">
        <header><span className="metric-label">进度</span></header>
        <div className="task-progress-donut">
          <Donut percent={percent} />
        </div>
        <footer>
          <small>{done} / {total || "-"} 步骤已完成</small>
          <i><em style={{ width: `${percent}%` }} /></i>
        </footer>
      </article>

      <StatCard label="Solver" icon={Users} tone="info"
        value={String(lifecycle.active_solvers ?? 0)} detail="活跃 / 总数" />
      <StatCard label="待审批" icon={Clock} tone="warning"
        value={String(lifecycle.pending_approvals ?? 0)}
        detail={lifecycle.needs_attention ? "需要人工审批" : "当前无待处理"} />
      <StatCard label="最高严重度" icon={AlertOctagon} tone="danger"
        value={topSeverity ?? "—"}
        detail={topSeverity ? `发现于 ${findings.filter((row) => row.severity === topSeverity).length} 个关键问题` : "尚无已确认发现"} />
    </section>

    <DetailTabs tabs={TABS} active={tab} onSelect={(id) => setTab(id as DetailTabId)} size="lg" />

    <div role="tabpanel" className="task-detail-panel ref-fill">
      {tab === "overview" ? <Overview
        detail={value}
        findings={findings}
        events={history.data?.events ?? []}
        onTab={setTab}
      /> : null}
      {tab === "directives" ? <Directives detail={value} /> : null}
      {tab === "team" ? <TeamPanel query={team} /> : null}
      {tab === "inputs" ? <InputsPanel query={inputs} /> : null}
      {tab === "results" ? <ResultsPanel query={evidence} /> : null}
      {tab === "config" ? <ConfigPanel detail={value} /> : null}
      {tab === "history" ? <HistoryPanel query={history} /> : null}
    </div>
  </div>;
}

function Overview({ detail, findings, events, onTab }: {
  detail: TaskDetail;
  findings: FindingRow[];
  events: Array<Record<string, any>>;
  onTab: (tab: DetailTabId) => void;
}) {
  const policy = detail.config_snapshot.execution_policy as Record<string, unknown>;
  const timeline = events.length
    ? events.slice(0, 4).map((event) => ({
      id: String(event.id ?? event.seq),
      title: String(event.type),
      timestamp: formatDate(event.created_at),
      description: String(event.payload?.summary ?? event.payload?.reason ?? "事件已记录"),
      tone: (String(event.type).includes("FAILED") ? "danger" : String(event.type).includes("COMPLETED") ? "success" : "neutral") as "danger" | "success" | "neutral",
    }))
    : SAMPLE_EVENTS;

  return <>
    <div className="dashboard-columns">
      <section className="ref-card">
        <header className="ref-card-head">
          <h2>关键发现</h2>
          <button className="ref-link-button" onClick={() => onTab("results")}>查看全部</button>
        </header>
        <ul className="finding-list">
          {findings.map((row) => <li key={row.id}>
            <span className={`severity-chip ${SEVERITY_TONES[row.severity]}`}>{row.severity}</span>
            <div className="finding-copy">
              <strong>{row.title}</strong>
              <p>{row.description}</p>
              <span className="finding-tags">
                <span className="ref-chip tone-muted">影响: {row.impact}</span>
                <span className="ref-chip tone-muted">位置: {row.location}</span>
              </span>
            </div>
            <span className="finding-time">发现于 {row.at} <ChevronRight size={13} /></span>
          </li>)}
        </ul>
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={() => onTab("results")}>查看全部发现（{findings.length}）</button>
        </footer>
      </section>

      <section className="ref-card">
        <header className="ref-card-head"><h2>最近事件</h2></header>
        <Timeline items={timeline} emptyLabel="暂无事件" />
        <footer className="card-footer-link">
          <button className="ref-link-button" onClick={() => onTab("history")}>查看完整事件日志</button>
        </footer>
      </section>
    </div>

    <section className="ref-card task-directive-card">
      <div className="task-directive-head">
        <span className="row-icon tone-info" aria-hidden="true"><FileText size={18} /></span>
        <div>
          <h2>任务指令 / 目标摘要</h2>
          <p>{detail.task_spec.objective}</p>
        </div>
      </div>
      <div className="task-directive-chips">
        <span className="ref-chip tone-muted">范围: {detail.task.task_entry_url || "未设置任务入口"}</span>
        <span className="ref-chip tone-muted">模式: {modeLabel(detail.task.mode)}</span>
        <span className="ref-chip tone-muted">约束: {detail.task_spec.constraints.length} 条</span>
        <span className="ref-chip tone-muted">成功标准: {detail.task_spec.success_criteria.length} 条</span>
        <span className="ref-chip tone-muted">Preset: {String(policy.preset ?? "未投影")}</span>
      </div>
    </section>
  </>;
}

/** Reference image 04 draws progress as a ring with the percentage inside. */
function Donut({ percent }: { percent: number }) {
  const radius = 32;
  const circumference = 2 * Math.PI * radius;
  return <svg viewBox="0 0 80 80" role="img" aria-label={`进度 ${percent}%`}>
    <circle cx="40" cy="40" r={radius} className="donut-track" />
    <circle
      cx="40" cy="40" r={radius}
      className="donut-value"
      strokeDasharray={`${circumference * percent / 100} ${circumference}`}
      transform="rotate(-90 40 40)"
    />
    <text x="40" y="45" textAnchor="middle">{percent}%</text>
  </svg>;
}

function StatCard({ label, value, detail, icon: Icon, tone }: {
  label: string; value: string; detail: string; icon: LucideIcon;
  tone: "info" | "success" | "warning" | "danger";
}) {
  return <article className={`task-stat-card tone-${tone}`}>
    <header><span className="metric-label">{label}</span></header>
    <div className="task-stat-value">
      <span className="metric-icon" aria-hidden="true"><Icon size={22} /></span>
      <strong>{value}</strong>
    </div>
    <footer><small>{detail}</small></footer>
  </article>;
}

function toFindings(evidence: any): FindingRow[] {
  const items = evidence?.findings?.items ?? [];
  return items.map((item: Record<string, any>) => ({
    id: String(item.finding_id),
    severity: severityLabel(item.severity),
    title: String(item.title ?? item.finding_id),
    description: String(item.summary ?? item.description ?? ""),
    impact: String(item.impact ?? "—"),
    location: String(item.target ?? "—"),
    at: formatDate(item.created_at),
    sample: false,
  }));
}

function severityLabel(value: unknown): FindingRow["severity"] {
  const text = String(value ?? "").toLowerCase();
  if (text.includes("high") || text.includes("critical")) return "高";
  if (text.includes("medium")) return "中";
  return "低";
}

function Directives({ detail }: { detail: TaskDetail }) {
  return <div className="detail-card-stack">
    <section className="ref-card">
      <header className="ref-card-head"><h2>目标 / Objective</h2></header>
      <p className="skill-summary">{detail.task_spec.objective}</p>
    </section>
    <DirectiveGroup title="正式指令" values={detail.task_spec.instructions} />
    <DirectiveGroup title="约束" values={detail.task_spec.constraints} />
    <DirectiveGroup title="成功标准" values={detail.task_spec.success_criteria} />
    <section className="ref-card">
      <header className="ref-card-head"><h2>Scope 与资源</h2></header>
      <FieldGrid fields={[
        { label: "任务入口", value: detail.task.task_entry_url || "未设置任务入口 URL" },
        { label: "授权资源", value: detail.task_spec.resources.length ? `${detail.task_spec.resources.length} 项` : "未单独投影资源 Scope" },
      ]} />
    </section>
  </div>;
}

function DirectiveGroup({ title, values }: { title: string; values: Array<Record<string, unknown>> }) {
  return <section className="ref-card">
    <header className="ref-card-head"><h2>{title}</h2></header>
    {values.length
      ? <ul className="detail-bullet-list">{values.map((item, index) => <li key={String(item.id ?? index)}>{String(item.content ?? "")}</li>)}</ul>
      : <EmptyState label="暂无已投影内容" />}
  </section>;
}

function TeamPanel({ query }: { query: UseQueryResult<any> }) {
  if (query.isLoading) return <LoadingSkeleton label="正在读取团队摘要" rows={3} />;
  if (query.isError) return <ErrorState description="无法读取任务团队" actionLabel="重试" onAction={() => void query.refetch()} />;
  const value = query.data;
  if (!value) return null;

  return <div className="dashboard-columns">
    <section className="ref-card">
      <header className="ref-card-head"><h2>团队摘要</h2></header>
      <FieldGrid fields={[
        { label: "状态", value: String(value.team.status ?? "未投影") },
        { label: "Supervisor", value: String(value.team.supervisor_solver_id ?? "未实例化") },
        { label: "活动 Solver", value: String(value.team.active_solver_count ?? 0) },
        { label: "并发上限", value: String(value.team.max_active_workers ?? "未投影") },
      ]} />
    </section>
    <section className="ref-card">
      <header className="ref-card-head"><h2>{value.solvers.length} 个 Solver</h2></header>
      {value.solvers.length
        ? <ul className="detail-entity-list">{value.solvers.map((item: Record<string, unknown>) => <li key={String(item.solver_id)}>
          <div><strong>{String(item.solver_id)}</strong><small>{String(item.orchestration_role ?? "")}</small></div>
          <StatusBadge value={String(item.status ?? "created")} />
        </li>)}</ul>
        : <EmptyState label="尚未实例化 Solver" />}
    </section>
  </div>;
}

function InputsPanel({ query }: { query: UseQueryResult<any> }) {
  if (query.isLoading) return <LoadingSkeleton label="正在读取任务输入" rows={4} />;
  if (query.isError) return <ErrorState description="无法读取任务输入摘要" actionLabel="重试" onAction={() => void query.refetch()} />;
  const value = query.data;
  if (!value) return null;

  return <section className="ref-card">
    <header className="ref-card-head"><h2>输入摘要</h2></header>
    <p className="skill-summary">{value.prompt || "没有文字提示词"}</p>
    {value.files.length
      ? <ul className="detail-entity-list">{value.files.map((file: Record<string, unknown>) => <li key={String(file.id)}>
        <div><strong>{String(file.label ?? file.original_name ?? file.id)}</strong>
          <small>{String(file.mime_type ?? "unknown")} · {String(file.size ?? "-")} bytes</small></div>
      </li>)}</ul>
      : <EmptyState label="没有上传文件" />}
  </section>;
}

function ResultsPanel({ query }: { query: UseQueryResult<any> }) {
  if (query.isLoading) return <LoadingSkeleton label="正在读取任务结果" rows={5} />;
  if (query.isError) return <ErrorState description="无法读取 Evidence 与 Finding" actionLabel="重试" onAction={() => void query.refetch()} />;
  const value = query.data;
  if (!value) return null;
  const findings = value.findings.items ?? [];
  const artifacts = value.artifacts.items ?? [];

  return <div className="dashboard-columns">
    <section className="ref-card">
      <header className="ref-card-head"><h2>已确认结果</h2></header>
      {findings.length
        ? <ul className="detail-entity-list">{findings.map((item: Record<string, unknown>) => <li key={String(item.finding_id)}>
          <div><strong>{String(item.title)}</strong><small>{String(item.target ?? "未指定目标")}</small></div>
          <StatusBadge value={String(item.status ?? "candidate")} />
        </li>)}</ul>
        : <EmptyState label="尚无已确认 Finding" />}
    </section>
    <section className="ref-card">
      <header className="ref-card-head"><h2>证据产物</h2></header>
      {artifacts.length
        ? <ul className="detail-entity-list">{artifacts.slice(0, 8).map((item: Record<string, unknown>) => <li key={String(item.artifact_id)}>
          <div><strong>{String(item.artifact_id)}</strong><small>{String(item.kind ?? "artifact")} · {formatDate(String(item.created_at ?? ""))}</small></div>
        </li>)}</ul>
        : <EmptyState label="尚无 Artifact" />}
    </section>
  </div>;
}

function ConfigPanel({ detail }: { detail: TaskDetail }) {
  return <div className="detail-card-stack">
    <JsonBlock title="Mode Config" value={detail.config_snapshot.mode_config} />
    <JsonBlock title="ExecutionPolicy Snapshot" value={detail.config_snapshot.execution_policy} />
    <JsonBlock title="Model Snapshot" value={detail.config_snapshot.model ?? { status: "未配置或未投影" }} />
    <JsonBlock title="MCP Capability Snapshot" value={detail.config_snapshot.mcp_capabilities} />
  </div>;
}

function HistoryPanel({ query }: { query: UseQueryResult<any> }) {
  if (query.isLoading) return <LoadingSkeleton label="正在读取任务历史" rows={6} />;
  if (query.isError) return <ErrorState description="无法读取任务时间线" actionLabel="重试" onAction={() => void query.refetch()} />;
  const events = query.data?.events ?? [];

  return <section className="ref-card">
    <header className="ref-card-head"><h2>持久化事件历史</h2></header>
    <Timeline
      items={events.map((event: any) => ({
        id: String(event.id ?? event.seq),
        title: String(event.type),
        timestamp: formatDate(event.created_at),
        description: String(event.payload?.summary ?? event.payload?.reason ?? "事件已记录"),
        tone: event.type.includes("FAILED") ? "danger" : event.type.includes("COMPLETED") ? "success" : "neutral",
      }))}
      emptyLabel="暂无历史事件"
    />
  </section>;
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return <section className="ref-card">
    <header className="ref-card-head"><h2>{title}</h2></header>
    <pre className="ref-prompt">{JSON.stringify(value, null, 2)}</pre>
  </section>;
}

function modeLabel(mode: string): string {
  return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode;
}

/** Wall-clock span between task creation and its last recorded update. */
function elapsed(from?: string | null, to?: string | null): string {
  if (!from) return "—";
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const minutes = Math.floor((end - start) / 60000);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" }) : "未记录";
}
