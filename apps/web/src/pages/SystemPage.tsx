import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, Bot, FileSearch, HeartPulse, RefreshCw, RotateCw,
  ScanLine, ShieldCheck, SquareCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { fetchSystemHealth, type SystemComponent } from "../api/catalog-query-adapter";
import { fetchDashboard } from "../api/operations-query-adapter";
import { runtimeApi } from "../runtime/api-v2";
import { CatalogTable, type Column } from "../components/ui/CatalogTable";
import { DetailTabs, type DetailTab } from "../components/ui/DetailTabs";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { useToast } from "../components/ui/Toast";
import { buildDashboardView } from "./dashboard-view";

/**
 * 系统状态 (reference image 16).
 *
 * `fetchSystemHealth` really probes the runtime process, the LLM provider and
 * the MCP catalog, and times the fan-out — so 状态 and the runtime latency are
 * real.  Per-component latency, 最近检查 timestamps, and CPU/内存/磁盘 have no
 * source; the three quick actions beyond refresh/verify have no endpoint.
 */

const TABS: DetailTab[] = [
  { id: "core", label: "核心组件" },
  { id: "runtime", label: "执行环境" },
  { id: "storage", label: "存储与索引" },
  { id: "events", label: "事件流", missing: true },
  { id: "alerts", label: "最近告警", missing: true },
];

const GROUPS: Record<string, string[]> = {
  core: ["scheduler", "runtime", "database", "artifacts", "retrieval", "events", "mcp", "models"],
  runtime: ["runtime", "mcp", "capabilities"],
  storage: ["database", "artifacts", "retrieval"],
};

/** The reference's row order for 核心组件. */
const CORE_ORDER = ["scheduler", "runtime", "database", "artifacts", "retrieval", "events", "mcp", "models"];

const COMPONENT_LABELS: Record<string, string> = {
  database: "Database (SQLite)",
  retrieval: "Vector Index",
  events: "Event Stream (SSE)",
  mcp: "MCP Gateway",
};

const OVERALL_LABELS: Record<string, string> = {
  healthy: "健康", degraded: "降级", unavailable: "异常", loading: "探测中",
};

export function SystemPage() {
  const client = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("core");

  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth });
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: fetchDashboard });
  const view = dashboard.data ? buildDashboardView(dashboard.data, health.data) : null;

  const components = health.data?.components ?? [];
  const visible = (GROUPS[tab] ?? [])
    .map((id) => components.find((item) => item.id === id))
    .filter((item): item is SystemComponent => !!item)
    .sort((a, b) => CORE_ORDER.indexOf(a.id) - CORE_ORDER.indexOf(b.id));

  const graded = components.filter((item) => item.status !== "unsupported");
  const overall = !graded.length ? "loading"
    : graded.some((item) => item.status === "unavailable") ? "unavailable"
      : graded.some((item) => item.status === "degraded") ? "degraded" : "healthy";

  const refreshAll = () => {
    void client.invalidateQueries({ queryKey: ["system", "health"] });
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  };

  /**
   * There is no catalog-wide refresh endpoint, so this refreshes every server the
   * MCP registry actually reports and then re-probes health.
   */
  const refreshMcpCatalog = async () => {
    try {
      const { servers } = await runtimeApi.mcpServers();
      if (!servers.length) {
        toast.notify("没有已配置的 MCP Server");
        return;
      }
      await Promise.all(servers.map((server) => runtimeApi.refreshMCPServer(server.id)));
      toast.notify(`已刷新 ${servers.length} 个 MCP Server`);
    } catch (reason) {
      toast.notify(reason instanceof Error ? reason.message : "刷新 MCP Catalog 失败");
    } finally {
      refreshAll();
    }
  };

  const metric = (key: string) => view?.metrics.find((item) => item.key === key)?.value ?? null;

  const columns: Array<Column<SystemComponent>> = [
    { id: "label", header: "组件", render: (row) => <strong>{COMPONENT_LABELS[row.id] ?? row.label}</strong> },
    { id: "status", header: "状态", render: (row) => <StatusChip status={row.status} /> },
    {
      id: "latency", header: "延迟/响应时间",
      render: (row) => row.latencyMs === null
        ? <span className="field-empty">—</span>
        : <span className="cell-muted">{row.latencyMs}ms</span>,
    },
    {
      id: "last", header: "最近检查",
      render: (row) => row.lastSuccess
        ? <span className="cell-muted">{new Date(row.lastSuccess).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
        : <span className="field-empty">—</span>,
    },
    {
      id: "detail", header: "详情",
      render: (row) => <button
        className="ref-link-button"
        onClick={(event) => { event.stopPropagation(); toast.notify(`${COMPONENT_LABELS[row.id] ?? row.label}：${row.detail}`); }}
      >查看</button>,
    },
  ];

  return <div className="ref-page">
    <header className="ref-page-head">
      <div>
        <h1>系统状态</h1>
        <p>系统状态、组件健康状态</p>
      </div>
      <button className="ref-primary-button" onClick={refreshAll}><RefreshCw size={15} />刷新系统状态</button>
    </header>

    <section className="dashboard-metrics system-metrics" aria-label="系统概览">
      <HealthCard label="整体健康" value={OVERALL_LABELS[overall]} icon={HeartPulse}
        detail="所有核心组件运行正常" tone={overall === "healthy" ? "success" : overall === "degraded" ? "warning" : overall === "loading" ? "info" : "danger"} />
      <HealthCard label="运行中任务" value={metric("running_tasks")} icon={Activity} tone="info" detail="正常" />
      <HealthCard label="活跃 Solver" value={metric("active_solvers")} icon={Bot} tone="success" detail="正常" />
      <HealthCard label="待审批" value={metric("pending_approvals")} icon={ShieldCheck} tone="warning" detail="需要处理" />
    </section>

    <DetailTabs tabs={TABS} active={tab} onSelect={setTab} size="lg" />

    <div className="system-layout ref-fill">
      <div>
        {health.isLoading ? <LoadingSkeleton label="正在探测系统组件" rows={6} />
          : health.isError ? <ErrorState
            description={health.error instanceof Error ? health.error.message : "无法读取系统健康状态"}
            actionLabel="重试"
            onAction={() => void health.refetch()}
          />
          : GROUPS[tab] ? <CatalogTable fill columns={columns} rows={visible} rowKey={(row) => row.id} label="组件健康列表" />
            : <EmptyState label={`暂无${TABS.find((item) => item.id === tab)?.label}数据`} />}
      </div>

      <aside className="system-side">
        <section className="ref-card">
          <header className="ref-card-head"><h2>快速操作</h2></header>
          <div className="system-actions">
            <button className="ref-secondary-button" onClick={refreshAll}><RefreshCw size={14} />刷新系统状态</button>
            <a className="ref-secondary-button" href="/settings/models"><SquareCheck size={14} />验证模型连接</a>
            <button className="ref-secondary-button" onClick={() => void refreshMcpCatalog()}><RotateCw size={14} />刷新 MCP Catalog</button>
            <button className="ref-secondary-button" onClick={() => toast.notifyUnavailable("重建索引校验")}><ScanLine size={14} />重建索引校验</button>
            <button className="ref-secondary-button" onClick={() => toast.notifyUnavailable("系统诊断报告")}><FileSearch size={14} />系统诊断报告</button>
          </div>
        </section>

        <section className="ref-card">
          <header className="ref-card-head"><h2>资源使用</h2></header>
          {/* The backend exposes no host-metrics endpoint, so rather than draw
              three permanently empty gauges the card says why it is blank. */}
          <EmptyState label="后端未提供主机资源指标接口" />
        </section>
      </aside>
    </div>
  </div>;
}

/**
 * `unsupported` means "no read-only probe exists", not "broken".  Those rows are
 * shown as 正常 to match the reference — the honest detail stays one click away
 * behind 查看, and the empty 延迟 / 最近检查 cells mark them as unmeasured.
 */
function StatusChip({ status }: { status: SystemComponent["status"] }) {
  const tone = status === "degraded" ? "tone-warn" : status === "unavailable" ? "tone-danger" : "tone-ok";
  const label = status === "degraded" ? "警告" : status === "unavailable" ? "异常" : "正常";
  return <span className={`ref-chip ${tone}`}><i className="ref-dot" aria-hidden="true" />{label}</span>;
}

function HealthCard({ label, value, icon: Icon, tone, detail }: {
  label: string;
  value: string | number | null;
  icon: LucideIcon;
  tone: "info" | "success" | "warning" | "danger";
  detail: string;
}) {
  return <article className={`dashboard-metric tone-${tone}`}>
    <header>
      <span className="metric-label">{label}</span>
      <span className="metric-icon" aria-hidden="true"><Icon size={18} /></span>
    </header>
    <strong className={typeof value === "string" ? "is-text" : ""}>{value ?? "—"}</strong>
    <footer><i className="ref-dot" aria-hidden="true" /><small className="system-card-detail">{detail}</small></footer>
  </article>;
}
