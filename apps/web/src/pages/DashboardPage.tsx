import { Activity, Bot, CircleAlert, Clock3, Play, ShieldCheck } from "lucide-react";
import type { DashboardResponse, OperationalTaskSummary } from "../api/operations-query-adapter";
import { EmptyState } from "../components/ui/EmptyState";
import { MetricCard } from "../components/ui/MetricCard";
import { PageHeader } from "../components/ui/PageHeader";
import { RiskBadge } from "../components/ui/RiskBadge";
import { StatusBadge } from "../shared/StatusBadge";
import { MODE_PROFILES } from "../modes";
import { BACKEND_CAPABILITIES } from "../api/capability-state";
import { CapabilityNotice, ProductEmpty } from "../components/ui/ProductPrimitives";

export function DashboardPage({ value, onNew, onTask, onRuntime, onApprovals }: {
  value: DashboardResponse;
  onNew: () => void;
  onTask: (taskId: string) => void;
  onRuntime: (taskId: string) => void;
  onApprovals: (taskId?: string) => void;
}) {
  const metric = (key: keyof DashboardResponse["metrics"]) => value.metrics[key] ?? "暂不可用";
  return <section className="page-stack operations-dashboard">
    <PageHeader
      eyebrow="OPERATIONS / OVERVIEW"
      title="运营总览"
      description="聚合运行状态、人工处理队列与最近结果；任务执行细节仍在各自工作区中按需加载。"
      breadcrumbs={[{ label: "TGA", href: "/" }, { label: "运营总览" }]}
      actions={<button onClick={onNew}>新建任务</button>}
    />
    <div className="operations-metrics">
      <MetricCard label="运行中任务" value={metric("running_tasks")} detail="当前 Session 状态" icon={Activity} tone="info" />
      <MetricCard label="等待审批" value={metric("pending_approvals")} detail="真实待处理审批" icon={ShieldCheck} tone="warning" />
      <MetricCard label="等待用户输入" value={metric("awaiting_user_input")} detail="Orchestration 状态" icon={Clock3} tone="warning" />
      <MetricCard label="阻塞任务" value={metric("blocked_tasks")} detail="需要检查原因" icon={CircleAlert} tone="danger" />
      <MetricCard label="活动 Solver" value={metric("active_solvers")} detail="运行及等待中的实例" icon={Bot} tone="success" />
    </div>

    <div className="operations-primary-grid dashboard-work-grid">
      <section className="operations-panel attention-panel">
        <header><div><span>ACTION REQUIRED</span><h2>需要你的处理</h2></div><button className="text-button" onClick={() => onApprovals()}>打开审批中心</button></header>
        {value.needs_attention.length ? <div className="attention-list">{value.needs_attention.map((item) => <button key={item.id} onClick={() => item.kind === "approval" ? onApprovals(item.task_id) : onTask(item.task_id)}>
          <span className={`attention-kind kind-${item.kind}`}>{item.kind === "approval" ? "审批" : item.kind === "user_input" ? "输入" : "阻塞"}</span>
          <div><strong>{item.title}</strong><p>{item.task_name} · {item.description}</p><small>{formatDate(item.updated_at)}</small></div>
          <div className="attention-badges"><StatusBadge value={item.status} />{item.risk ? <RiskBadge value={item.risk} /> : null}</div>
        </button>)}</div> : <EmptyState label="当前没有需要人工处理的任务。" />}
      </section>
      <section className="operations-panel active-work-panel">
        <header><div><span>ACTIVE WORK</span><h2>活动任务</h2></div><a href="/tasks">查看全部</a></header>
        {value.active_tasks.length ? <div className="operations-task-grid dashboard-active-grid">{value.active_tasks.map((task) => <TaskSummaryCard key={task.task_id} task={task} onOpen={onTask} onRuntime={onRuntime} />)}</div> : <EmptyState label="当前没有活动任务。" />}
      </section>
    </div>

    <div className="dashboard-bottom-grid">
      <section className="operations-panel recent-results"><header><div><span>RECENT COMPLETED</span><h2>最近完成任务</h2></div></header>{value.recent_completed.length ? <div className="recent-result-list">{value.recent_completed.map((task) => <button key={task.task_id} onClick={() => onTask(task.task_id)}><StatusBadge value={task.status} /><div><strong>{task.name}</strong><small>{task.findings} 个 Finding · {task.artifacts} 个 Artifact · {formatDate(task.updated_at)}</small></div></button>)}</div> : <EmptyState label="尚无最近完成任务或确认结果。" />}</section>
      <section className="operations-panel confirmed-results"><header><div><span>CONFIRMED RESULTS</span><h2>最近确认结果</h2></div></header><CapabilityNotice state={BACKEND_CAPABILITIES.confirmedResults.state} reason={BACKEND_CAPABILITIES.confirmedResults.reason} /><ProductEmpty title="暂无独立结果流" description="保留该区域，未从任务摘要推断或伪造确认结果。" /></section>
      <section className="operations-panel system-summary"><header><div><span>SYSTEM SIGNALS</span><h2>系统状态</h2></div><a href="/system">查看详情</a></header><div>{value.system_status.map((item) => <article key={item.id}><StatusBadge value={item.status} /><div><strong>{item.label}</strong><small>{item.detail}</small></div></article>)}</div></section>
    </div>
  </section>;
}

function TaskSummaryCard({ task, onOpen, onRuntime }: { task: OperationalTaskSummary; onOpen: (id: string) => void; onRuntime: (id: string) => void }) {
  const mode = MODE_PROFILES[task.mode as keyof typeof MODE_PROFILES];
  const progress = task.intent_total ? Math.round(task.intent_completed / task.intent_total * 100) : 0;
  return <article className="operations-task-card">
    <header><StatusBadge value={task.status} /><small>{mode?.label ?? task.mode}</small></header>
    <button className="operations-task-main" onClick={() => onOpen(task.task_id)}><h3>{task.name}</h3><p>{task.task_id}</p></button>
    <div className="operations-task-stats"><span><b>{task.intent_completed}/{task.intent_total || "-"}</b>Intent</span><span><b>{task.active_solvers}</b>Solver</span><span><b>{task.findings}</b>Finding</span></div>
    <div className="operations-progress"><span>Intent 进度</span><b>{progress}%</b><i><em style={{ width: `${progress}%` }} /></i></div>
    <footer><small>{task.latest_event?.type ?? "等待运行事件"}</small><button className="secondary-button" onClick={() => onRuntime(task.task_id)}><Play size={13} />进入运行</button></footer>
  </article>;
}

function formatDate(value: string) {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "暂无时间";
}
