import { Bell, ChevronLeft, ChevronRight, Menu, Search, Wifi } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { NAVIGATION_GROUPS, isNavigationItemActive } from "./navigation";
import { isRuntimePage, type AppRoute } from "./router";

export function AppShell({ route, children }: { route: AppRoute; children: ReactNode }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const go = (path: string) => {
    setMobileOpen(false);
    navigate(path);
  };

  return <div className={`app-shell ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}>
    <aside className="app-sidebar" aria-label="应用侧栏">
      <div className="app-brand">
        <button className="app-brand-mark" aria-label="返回首页" onClick={() => go("/")}>T</button>
        <div className="app-brand-copy"><strong>TGA</strong><span>Team Gipsy Agent</span></div>
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
              <Icon size={17} aria-hidden="true" />
              <span>{item.label}</span>
            </button>;
          })}
        </section>)}
      </nav>

      <footer className="app-sidebar-footer"><span className="connection-dot" aria-hidden="true" /><span>本地控制台</span></footer>
    </aside>

    {mobileOpen ? <button className="app-sidebar-backdrop" aria-label="关闭导航" onClick={() => setMobileOpen(false)} /> : null}

    <div className="app-content">
      <header className="app-topbar">
        <button className="app-mobile-menu" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu size={19} /></button>
        <label className="global-search" title="全局搜索将在后续阶段接入">
          <Search size={16} aria-hidden="true" />
          <input aria-label="全局搜索" placeholder="搜索任务、Solver 或 Artifact" disabled />
          <kbd>Ctrl K</kbd>
        </label>
        <div className="app-topbar-actions">
          <span className="topbar-connection" title="连接状态由当前页面按需检测"><Wifi size={15} /><span>按需连接</span></span>
          <button aria-label="通知" title="通知中心将在后续阶段接入" disabled><Bell size={17} /></button>
          <button className="operator-menu" aria-label="用户菜单" title="本地操作员"><span>本</span><b>本地操作员</b></button>
        </div>
      </header>
      <main className={`app-main app-page ${isRuntimePage(route.page) ? "runtime-main" : ""}`}>
        {children}
      </main>
    </div>
  </div>;
}
