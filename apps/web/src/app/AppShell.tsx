import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronDown, ChevronLeft, ChevronRight, CirclePlay, CircleHelp, Menu, Search, Shield } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { fetchDashboard } from "../api/operations-query-adapter";
import { buildDashboardView } from "../pages/dashboard-view";
import { NAVIGATION_GROUPS, isNavigationItemActive } from "./navigation";
import { isRuntimePage, type AppRoute } from "./router";

export function AppShell({ route, children }: { route: AppRoute; children: ReactNode }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Shares the dashboard query key, so the shell reuses the page's cache
  // instead of issuing a second aggregate request.  It also builds the same
  // view model, so the sidebar and topbar can never disagree with the page.
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: fetchDashboard });
  const view = dashboard.data ? buildDashboardView(dashboard.data) : null;
  const running = (view?.runningTasks ?? []).slice(0, 3);
  const pending = view?.metrics.find((metric) => metric.key === "pending_approvals")?.value ?? 0;

  const go = (path: string) => {
    setMobileOpen(false);
    navigate(path);
  };

  return <div className={`app-shell ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}>
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
          key={task.taskId}
          onClick={() => go(`/tasks/${encodeURIComponent(task.taskId)}/runtime`)}
        >
          <CirclePlay size={15} aria-hidden="true" />
          <span>
            <strong>{task.name}</strong>
            <small>运行中 · {task.percent}%</small>
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

    {mobileOpen ? <button className="app-sidebar-backdrop" aria-label="关闭导航" onClick={() => setMobileOpen(false)} /> : null}

    <div className="app-content">
      <header className="app-topbar">
        <button className="app-mobile-menu" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu size={19} /></button>
        <label className="global-search" title="全局搜索">
          <Search size={16} aria-hidden="true" />
          <input aria-label="全局搜索" placeholder="全局搜索（Ctrl+K）" disabled />
          <kbd>Ctrl K</kbd>
        </label>
        <div className="app-topbar-actions">
          <span className="topbar-status" title="当前是否有运行中的任务">
            <i className={running.length ? "running-dot" : "idle-dot"} aria-hidden="true" />
            {running.length ? "运行中" : "空闲"}
            <ChevronDown size={14} aria-hidden="true" />
          </span>
          <button className="topbar-icon topbar-bell" aria-label={`通知，${pending} 条待审批`} title="通知中心" disabled>
            <Bell size={18} />
            {pending > 0 ? <b className="topbar-badge">{pending}</b> : null}
          </button>
          <button className="topbar-icon" aria-label="帮助" title="帮助" disabled><CircleHelp size={18} /></button>
          <button className="topbar-user" aria-label="用户菜单" title="本地控制台，暂无登录体系">
            <span className="topbar-avatar" aria-hidden="true">A</span>
            <b>Admin</b>
            <ChevronDown size={14} aria-hidden="true" />
          </button>
        </div>
      </header>
      <main className={`app-main app-page ${isRuntimePage(route.page) ? "runtime-main" : ""}`}>
        {children}
      </main>
    </div>
  </div>;
}
