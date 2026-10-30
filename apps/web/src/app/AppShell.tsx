import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronLeft, ChevronRight, CirclePlay, HeartPulse, Shield } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { attentionApi } from "../api/tga3-attention";
import { fetchSystemHealth } from "../api/tga3-system";
import { listTasks } from "../api/tga3-tasks";
import { NAVIGATION_GROUPS, isNavigationItemActive } from "./navigation";
import { isRuntimePage, type AppRoute } from "./router";
import { statusLabel } from "../shared/status";

export function AppShell({ route, children }: { route: AppRoute; children: ReactNode }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);

  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const attention = useQuery({ queryKey: ["tga3", "attention"], queryFn: attentionApi.list, refetchInterval: 5000 });
  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth, refetchInterval: 15000 });
  const activeTasks = (tasks.data ?? []).filter((task) => ["scouting", "starting", "running", "finalizing", "reporting"].includes(task.state));
  const running = activeTasks.slice(0, 3);
  const pending = attention.data?.length ?? 0;
  const healthStatus = overallHealth(health.data?.components.map((item) => item.status), health.isPending, health.isError);

  const go = (path: string) => {
    navigate(path);
  };

  return <div className={`app-shell ${collapsed ? "is-collapsed" : ""}`}>
    <aside className="app-sidebar" aria-label="应用侧栏">
      <div className="app-brand">
        <button className="app-brand-mark" aria-label="返回首页" onClick={() => go("/")}><Shield size={20} aria-hidden="true" /></button>
        <div className="app-brand-copy"><strong>TGA</strong></div>
        <button className="app-collapse" aria-label={collapsed ? "展开导航" : "折叠导航"} onClick={() => setCollapsed((value) => !value)}>
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      <nav className="app-navigation" aria-label="主导航">
        {NAVIGATION_GROUPS.map((group) => <section key={group.id} aria-labelledby={`nav-${group.id}`}>
          <h2 id={`nav-${group.id}`}>{group.label}</h2>
          {group.items.map((item) => {
            const Icon = item.icon;
            const active = isNavigationItemActive(item, route.page);
            return <button
              key={item.id}
              className={active ? "active" : ""}
              aria-current={active ? "page" : undefined}
              title={item.tooltip ?? item.label}
              onClick={() => go(item.path)}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
            </button>;
          })}
        </section>)}
      </nav>

      {running.length ? <section className="sidebar-running" aria-label="正在运行">
        <h2>正在运行 <i className="running-dot" aria-hidden="true" /></h2>
        {running.map((task) => <button
          key={task.id}
          onClick={() => go(`/tasks/${encodeURIComponent(task.id)}/runtime`)}
        >
          <CirclePlay size={15} aria-hidden="true" />
          <span>
            <strong>{task.title}</strong>
            <small>{statusLabel(task.state)}</small>
          </span>
        </button>)}
      </section> : null}

      <footer className="app-sidebar-footer">
        <button className="app-collapse-footer" onClick={() => setCollapsed((value) => !value)}>
          <ChevronLeft size={15} aria-hidden="true" />
          <span>收起侧栏</span>
        </button>
      </footer>
    </aside>

    <div className="app-content">
      <header className="app-topbar">
        <span className="app-breadcrumb">{["models", "skills", "solvers", "policies", "system"].includes(route.page) ? "配置中心" : "工作区"} / {({ dashboard: "首页", tasks: "任务", new: "创建任务", runtime: "任务工作区", approvals: "审批中心", reports: "报告", solvers: "Solver", skills: "Skills", models: "Models", policies: "场景", system: "系统状态", "not-found": "页面不存在" })[route.page]}</span>
        <div className="app-topbar-actions">
          <button className="topbar-state" title={activeTasks.length ? `${activeTasks.length} 个任务正在运行` : "当前没有运行中的任务"} onClick={() => go("/tasks")}>
            <i className={activeTasks.length ? "running-dot" : "idle-dot"} aria-hidden="true" />
            <span>{activeTasks.length ? "运行中" : "空闲"}</span>
          </button>
          <button className={`topbar-health health-${healthStatus}`} title="查看系统健康状态" onClick={() => go("/system")}>
            <HeartPulse size={17} aria-hidden="true" />
            <span>{healthLabel(healthStatus)}</span>
          </button>
          <button className="topbar-icon topbar-bell" aria-label={`审批中心，${pending} 条待处理`} title="审批中心" onClick={() => go("/approvals")}>
            <Bell size={18} aria-hidden="true" />
            {pending > 0 ? <i className="topbar-notification-dot" aria-hidden="true" /> : null}
          </button>
        </div>
      </header>
      <main className={`app-main app-page ${isRuntimePage(route.page) ? "runtime-main" : ""}`}>
        {children}
      </main>
    </div>
  </div>;
}

type OverallHealth = "loading" | "healthy" | "degraded" | "unavailable";
function overallHealth(statuses: Array<"healthy" | "available" | "degraded" | "unavailable"> | undefined, loading: boolean, failed: boolean): OverallHealth {
  if (failed) return "unavailable";
  if (loading || !statuses?.length) return "loading";
  if (statuses.includes("unavailable")) return "unavailable";
  if (statuses.includes("degraded")) return "degraded";
  return "healthy";
}
function healthLabel(status: OverallHealth) {
  return ({ loading: "检查中", healthy: "健康", degraded: "降级", unavailable: "异常" } as const)[status];
}
