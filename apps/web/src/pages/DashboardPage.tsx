import { Bot, ChevronRight, CircleCheck, CirclePlay, Clock, Database, FileText, Layers } from "lucide-react";
import type { AttentionItem } from "../api/tga3-attention";
import type { SystemHealth } from "../api/tga3-system";
import type { TGA3TaskListItem } from "../api/tga3-tasks";
import { EmptyState } from "../components/ui/EmptyState";
import { MODE_PROFILES } from "../modes";
import { statusLabel } from "../shared/status";

export function DashboardPage({ tasks, attention, health, onNew, onTask, onTasks, onApprovals, onSystem, onReports }: {
  tasks: TGA3TaskListItem[];
  attention: AttentionItem[];
  health?: SystemHealth;
  onNew: () => void;
  onTask: (taskId: string) => void;
  onTasks: () => void;
  onApprovals: () => void;
  onSystem: () => void;
  onReports: () => void;
}) {
  const active = tasks.filter((task) => ["starting", "running", "waiting_user", "finalizing", "reporting"].includes(task.state));
  const completed = tasks.filter((task) => task.state === "completed");
  const failed = tasks.filter((task) => task.state === "failed");
  const metrics = [
    { key: "running", label: "活动任务", value: active.length, icon: CirclePlay, tone: "info" },
    { key: "attention", label: "需要处理", value: attention.length, icon: Clock, tone: "warning" },
    { key: "completed", label: "已完成", value: completed.length, icon: CircleCheck, tone: "success" },
    { key: "failed", label: "失败任务", value: failed.length, icon: Bot, tone: "danger" },
  ];

  return <div className="ref-page dashboard-ref">
    <header className="dashboard-greeting"><div><h1>TGA3 控制台</h1><p>双 Worker 黑板任务、待处理事项与最终报告</p></div><button className="ref-primary-button" onClick={onNew}>创建任务</button></header>
    <section className="dashboard-metrics" aria-label="运行指标">{metrics.map(({ key, label, value, icon: Icon, tone }) => <article key={key} className={`dashboard-metric tone-${tone}`}><header><span className="metric-icon"><Icon size={17} /></span><span className="metric-label">{label}</span></header><strong>{value}</strong><footer>来自 TGA3 当前状态</footer></article>)}</section>
    <div className="dashboard-columns">
      <section className="ref-card"><header className="ref-card-head"><h2>需要你的处理</h2></header>{attention.length ? <ul className="attention-items">{attention.slice(0, 6).map((item) => <li key={item.id}><span className={`severity-chip ${item.kind.includes("failed") ? "tone-high" : "tone-medium"}`}>{item.kind === "question" ? "问答" : item.kind === "agent_paused" ? "暂停" : "失败"}</span><div className="attention-copy"><strong>{item.title}</strong><small>{item.task_title}{item.agent_id ? ` · ${item.agent_id}` : ""}</small></div><time>{relativeTime(item.created_at)}</time><button className="ref-secondary-button" onClick={() => onTask(item.task_id)}>处理</button></li>)}</ul> : <EmptyState label="暂无需要处理的事项" />}<footer className="card-footer-link"><button className="ref-link-button" onClick={onApprovals}>查看全部（{attention.length}）<ChevronRight size={14} /></button></footer></section>
      <section className="ref-card"><header className="ref-card-head"><h2>活动任务</h2></header>{active.length ? <ul className="active-task-items">{active.slice(0, 6).map((task) => <li key={task.id}><button className="active-task-row" onClick={() => onTask(task.id)}><span className="active-task-icon"><Bot size={16} /></span><span className="active-task-name"><strong>{task.title}</strong><small>{modeLabel(task.scene_id)} · {statusLabel(task.state)}</small></span><span className="active-task-stats"><span className="active-task-stat"><small>黑板序号</small><b>{task.blackboard_seq ?? 0}</b></span><span className="active-task-stat"><small>对话序号</small><b>{task.dialogue_seq ?? 0}</b></span></span></button></li>)}</ul> : <EmptyState label="暂无活动任务" />}<footer className="card-footer-link"><button className="ref-link-button" onClick={onTasks}>查看全部任务 <ChevronRight size={14} /></button></footer></section>
    </div>
    <div className="dashboard-columns">
      <section className="ref-card"><header className="ref-card-head"><h2>系统状态</h2></header><ul className="system-rows">{(health?.components ?? []).map((item) => <li key={item.id}><span className="system-row-icon">{item.id === "models" ? <Layers size={16} /> : <Database size={16} />}</span><strong>{item.label}</strong><span className="system-row-status"><small className="system-row-note">{item.detail}</small><em className={`system-row-value tone-${item.status === "unavailable" ? "bad" : item.status === "degraded" ? "warn" : "ok"}`}>{item.status === "unavailable" ? "异常" : item.status === "degraded" ? "降级" : "正常"}</em></span></li>)}</ul><footer className="card-footer-link"><button className="ref-link-button" onClick={onSystem}>查看系统状态 <ChevronRight size={14} /></button></footer></section>
      <section className="ref-card"><header className="ref-card-head"><h2>最近完成</h2></header>{completed.length ? <ul className="report-rows">{completed.slice(0, 6).map((task) => <li key={task.id}><span className="report-icon"><FileText size={16} /></span><button className="report-main" onClick={() => onTask(task.id)}><strong>{task.title}</strong></button><span className="chip tone-neutral">{modeLabel(task.scene_id)}</span><time>{relativeTime(task.updated_at)}</time><em className="report-status">完成</em></li>)}</ul> : <EmptyState label="暂无已完成任务" />}<footer className="card-footer-link"><button className="ref-link-button" onClick={onReports}>查看报告 <ChevronRight size={14} /></button></footer></section>
    </div>
  </div>;
}

function modeLabel(mode: string) { return MODE_PROFILES[mode as keyof typeof MODE_PROFILES]?.label ?? mode; }
function relativeTime(value: string) {
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return new Date(value).toLocaleDateString("zh-CN");
}
