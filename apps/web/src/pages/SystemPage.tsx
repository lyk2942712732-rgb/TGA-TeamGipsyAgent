import { useQuery } from "@tanstack/react-query";
import { Activity, Bot, HeartPulse, RefreshCw, SquareCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { attentionApi } from "../api/tga3-attention";
import { fetchSystemHealth, type SystemComponent } from "../api/tga3-system";
import { listTasks } from "../api/tga3-tasks";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

export function SystemPage() {
  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth });
  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const attention = useQuery({ queryKey: ["tga3", "attention"], queryFn: attentionApi.list });
  const components = health.data?.components ?? [];
  const overall = !components.length ? "loading" : components.some((item) => item.status === "unavailable") ? "unavailable" : components.some((item) => item.status === "degraded") ? "degraded" : "healthy";
  const running = (tasks.data ?? []).filter((task) => ["starting", "running", "finalizing", "reporting"].includes(task.state)).length;
  const waiting = (tasks.data ?? []).filter((task) => task.state === "waiting_user").length;
  const refresh = () => { void health.refetch(); void tasks.refetch(); void attention.refetch(); };
  const columns: Array<Column<SystemComponent>> = [
    { id: "label", header: "组件", render: (row) => <strong>{row.label}</strong> },
    { id: "status", header: "状态", render: (row) => <span className={`ref-chip ${row.status === "unavailable" ? "tone-danger" : row.status === "degraded" ? "tone-warn" : "tone-ok"}`}>{row.status === "unavailable" ? "异常" : row.status === "degraded" ? "降级" : "正常"}</span> },
    { id: "latency", header: "响应时间", render: (row) => row.latencyMs === null ? "—" : `${row.latencyMs}ms` },
    { id: "detail", header: "详情", render: (row) => <span className="cell-muted">{row.detail}</span> },
  ];
  return <div className="ref-page">
    <header className="ref-page-head"><div><h1>系统状态</h1><p>只展示 TGA3 控制面、模型、Skills 和动态 Worker 状态。</p></div><button className="ref-primary-button" onClick={refresh}><RefreshCw size={15} />刷新</button></header>
    <section className="dashboard-metrics system-metrics"><HealthCard label="整体健康" value={overall === "healthy" ? "健康" : overall === "loading" ? "探测中" : overall === "degraded" ? "降级" : "异常"} icon={HeartPulse} tone={overall === "healthy" ? "success" : overall === "loading" ? "info" : "danger"} detail="TGA3 原生健康检查" /><HealthCard label="活动任务" value={running} icon={Activity} tone="info" detail="starting / running / finalizing / reporting" /><HealthCard label="等待用户" value={waiting} icon={Bot} tone="warning" detail="waiting_user" /><HealthCard label="待处理事项" value={attention.data?.length ?? 0} icon={Bot} tone="warning" detail="Q&A、暂停与失败" /></section>
    <div className="system-layout ref-fill"><div>{health.isLoading ? <LoadingSkeleton label="正在探测系统组件" rows={6} /> : health.isError ? <ErrorState description={health.error instanceof Error ? health.error.message : "无法读取系统健康状态"} actionLabel="重试" onAction={() => void health.refetch()} /> : <CatalogTable fill columns={columns} rows={components} rowKey={(row) => row.id} label="组件健康列表" />}</div><aside className="system-side"><section className="ref-card"><header className="ref-card-head"><h2>配置入口</h2></header><div className="system-actions"><button className="ref-secondary-button" onClick={refresh}><RefreshCw size={14} />刷新系统状态</button><a className="ref-secondary-button" href="/settings/models"><SquareCheck size={14} />配置模型</a></div></section></aside></div>
  </div>;
}

function HealthCard({ label, value, icon: Icon, tone, detail }: { label: string; value: string | number; icon: LucideIcon; tone: "info" | "success" | "warning" | "danger"; detail: string }) {
  return <article className={`dashboard-metric tone-${tone}`}><header><span className="metric-label">{label}</span><span className="metric-icon"><Icon size={18} /></span></header><strong className={typeof value === "string" ? "is-text" : ""}>{value}</strong><footer><small>{detail}</small></footer></article>;
}
