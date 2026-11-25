import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bell,
  Bot,
  Command,
  ExternalLink,
  HeartPulse,
  PanelLeft,
  PanelTop,
  Play,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { attentionApi, type AttentionItem } from "../api/tga3-attention";
import { fetchSystemHealth } from "../api/tga3-system";
import { listTasks } from "../api/tga3-tasks";
import {
  APP_THEMES,
  applyNavMode,
  applyTheme,
  loadNavMode,
  loadTheme,
  saveNavMode,
  saveTheme,
  type AppTheme,
  type NavLayoutMode,
} from "../styles/theme";
import {
  BOTTOM_NAV_ITEMS,
  isNavigationItemActive,
  PRIMARY_NAV_ITEMS,
  SETTINGS_SUBNAV_ITEMS,
} from "./navigation";
import { isRuntimePage, type AppPage, type AppRoute } from "./router";
import { statusLabel } from "../shared/status";

const PAGE_TITLES: Record<AppPage, string> = {
  dashboard: "作战总览",
  tasks: "任务调度档案",
  new: "新建任务发射台",
  runtime: "100vh 作战室",
  approvals: "中断与审批",
  reports: "复盘与经验库",
  solvers: "舰队装配",
  skills: "武器库 IDE",
  models: "模型与网关",
  policies: "场景与策略",
  system: "系统诊断",
  "not-found": "页面不存在",
};

export function AppShell({ route, children }: { route: AppRoute; children: ReactNode }) {
  const navigate = useNavigate();
  const [theme, setTheme] = useState<AppTheme>(() => loadTheme());
  const [navMode, setNavMode] = useState<NavLayoutMode>(() => loadNavMode());
  const [cmdkOpen, setCmdkOpen] = useState(false);
  const [cmdkQuery, setCmdkQuery] = useState("");
  const [healthOpen, setHealthOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [resumingId, setResumingId] = useState("");
  const [drawerError, setDrawerError] = useState("");
  const cmdkInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    applyNavMode(navMode);
  }, [navMode]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setHealthOpen(false);
        setDrawerOpen(false);
        setCmdkOpen((open) => !open);
        setCmdkQuery("");
      } else if (event.key === "Escape") {
        setCmdkOpen(false);
        setHealthOpen(false);
        setDrawerOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (cmdkOpen) {
      window.setTimeout(() => cmdkInputRef.current?.focus(), 10);
    }
  }, [cmdkOpen]);

  const chooseTheme = (next: AppTheme) => {
    setTheme(next);
    saveTheme(next);
  };

  const toggleNavMode = () => {
    const next: NavLayoutMode = navMode === "rail" ? "topbar" : "rail";
    setNavMode(next);
    saveNavMode(next);
  };

  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const attention = useQuery({ queryKey: ["tga3", "attention"], queryFn: attentionApi.list, refetchInterval: 5000 });
  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth, refetchInterval: 15000 });

  const allTasks = tasks.data ?? [];
  const activeTasks = allTasks.filter((task) =>
    ["scouting", "starting", "running", "finalizing", "reporting"].includes(task.state),
  );
  const attentionItems = attention.data ?? [];
  const pending = attentionItems.length;
  const components = health.data?.components ?? [];
  const healthStatus = overallHealth(
    components.map((item) => item.status),
    health.isPending,
    health.isError,
  );

  const go = (path: string) => {
    setCmdkOpen(false);
    setHealthOpen(false);
    setDrawerOpen(false);
    navigate(path);
  };

  async function resumeAttention(item: AttentionItem) {
    if (!item.agent_id || resumingId) return;
    setResumingId(item.id);
    setDrawerError("");
    try {
      await attentionApi.resume(item.task_id, item.agent_id);
      await attention.refetch();
    } catch (reason) {
      setDrawerError(reason instanceof Error ? reason.message : "恢复 Agent 失败");
    } finally {
      setResumingId("");
    }
  }

  const commandItems = useMemo(() => {
    const q = cmdkQuery.trim().toLowerCase();
    const list: Array<{ id: string; group: string; label: string; meta: string; run: () => void }> = [
      { id: "nav-home", group: "空间", label: "作战总览", meta: "/", run: () => go("/") },
      { id: "nav-tasks", group: "空间", label: "任务调度档案", meta: "/tasks", run: () => go("/tasks") },
      { id: "nav-new", group: "空间", label: "新建任务发射台", meta: "/tasks/new", run: () => go("/tasks/new") },
      { id: "nav-reports", group: "空间", label: "复盘与经验库", meta: "/reports", run: () => go("/reports") },
      { id: "nav-skills", group: "空间", label: "武器库 IDE (Skills)", meta: "/settings/skills", run: () => go("/settings/skills") },
      ...SETTINGS_SUBNAV_ITEMS.map((item) => ({
        id: `set-${item.id}`,
        group: "设置",
        label: `设置 · ${item.label}`,
        meta: item.path,
        run: () => go(item.path),
      })),
      ...APP_THEMES.map((item) => ({
        id: `theme-${item.id}`,
        group: "风格",
        label: `切换风格 · ${item.code} ${item.label}`,
        meta: item.id,
        run: () => {
          chooseTheme(item.id);
          setCmdkOpen(false);
        },
      })),
      {
        id: "toggle-nav",
        group: "布局",
        label: `切换导航布局（当前：${navMode === "rail" ? "48px 左侧轨" : "顶部导航栏"}）`,
        meta: navMode === "rail" ? "TOPBAR" : "RAIL",
        run: () => {
          toggleNavMode();
          setCmdkOpen(false);
        },
      },
      ...allTasks.slice(0, 12).map((task) => ({
        id: `task-${task.id}`,
        group: "任务",
        label: task.title,
        meta: `${statusLabel(task.state)} · #${task.id.slice(0, 8)}`,
        run: () => go(`/tasks/${encodeURIComponent(task.id)}/runtime`),
      })),
    ];
    if (!q) return list;
    return list.filter((item) => `${item.group} ${item.label} ${item.meta}`.toLowerCase().includes(q));
  }, [cmdkQuery, allTasks, navMode]);

  const inSettings = ["models", "solvers", "policies", "system"].includes(route.page);

  return (
    <div className={`app-shell nav-${navMode}`}>
      {/* Left 48px Activity Rail */}
      {navMode === "rail" ? (
      <aside className="app-rail" aria-label="应用侧轨">
        <button
          type="button"
          className="app-brand-mark"
          aria-label="返回首页"
          title="TGA3 Ops · 作战总览"
          onClick={() => go("/")}
        >
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <rect x="2.5" y="2.5" width="15" height="15" rx="1.5" />
            <path d="M6 7h8M10 7v7" stroke="var(--c-accent)" strokeWidth="2" />
          </svg>
        </button>

        <nav className="app-rail-nav" aria-label="主导航">
          {PRIMARY_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = isNavigationItemActive(item, route.page);
            return (
              <button
                key={item.id}
                type="button"
                className={`rail-item${active ? " is-active" : ""}`}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                title={item.tooltip ?? item.label}
                onClick={() => go(item.path)}
              >
                <Icon size={17} aria-hidden="true" />
                {item.id === "tasks" && activeTasks.length > 0 ? <i className="rail-live-dot" aria-hidden="true" /> : null}
              </button>
            );
          })}
        </nav>

        <div className="app-rail-spacer" />
        <div className="app-rail-sep" aria-hidden="true" />

        <div className="app-rail-bottom">
          {BOTTOM_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = isNavigationItemActive(item, route.page);
            return (
              <button
                key={item.id}
                type="button"
                className={`rail-item${active ? " is-active" : ""}${item.accent ? " is-cta" : ""}`}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                title={item.tooltip ?? item.label}
                onClick={() => go(item.path)}
              >
                <Icon size={17} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      </aside>
      ) : null}

      {/* Right Main Column */}
      <div className="app-content">
        <header className="app-topbar">
          {navMode === "topbar" ? (
            <>
              <button
                type="button"
                className="topbar-brand-inline"
                aria-label="TGA3 首页"
                onClick={() => go("/")}
              >
                <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                  <rect x="2.5" y="2.5" width="15" height="15" rx="1.5" />
                  <path d="M6 7h8M10 7v7" stroke="var(--c-accent)" strokeWidth="2" />
                </svg>
                <span>TGA3</span>
              </button>

              <nav className="topbar-inline-nav" aria-label="主导航">
                {[...PRIMARY_NAV_ITEMS, ...BOTTOM_NAV_ITEMS].map((item) => {
                  const active = isNavigationItemActive(item, route.page);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`topbar-nav-link${active ? " is-active" : ""}${item.accent ? " is-cta" : ""}`}
                      aria-current={active ? "page" : undefined}
                      onClick={() => go(item.path)}
                    >
                      {item.shortLabel ?? item.label}
                    </button>
                  );
                })}
              </nav>
            </>
          ) : null}

          <span className="app-breadcrumb">
            <strong className="crumb-root">TGA3</strong>
            <span className="crumb-sep">/</span>
            {inSettings ? (
              <>
                <span className="crumb-parent">设置</span>
                <span className="crumb-sep">/</span>
              </>
            ) : null}
            <span className="crumb-curr">{PAGE_TITLES[route.page]}</span>
          </span>

          <div className="topbar-spacer" />

          {/* 4-Style Theme Switcher */}
          <div className="topbar-theme-dock" role="group" aria-label="界面主题">
            {APP_THEMES.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`theme-chip${theme === item.id ? " is-active" : ""}`}
                aria-pressed={theme === item.id}
                title={`${item.code} ${item.label}`}
                onClick={() => chooseTheme(item.id)}
              >
                <i
                  className="theme-swatch"
                  style={{
                    background: item.swatch,
                    border: item.swatchBorder ? `1px solid ${item.swatchBorder}` : undefined,
                  }}
                  aria-hidden="true"
                />
                <span>{item.short}</span>
              </button>
            ))}
          </div>

          {/* Dual Nav Layout Switcher */}
          <button
            type="button"
            className="topbar-btn topbar-nav-toggle"
            aria-label={`切换导航布局，当前${navMode === "rail" ? "左侧轨" : "顶部栏"}`}
            title="切换 48px 左侧极窄轨 / 顶部极简导航栏"
            onClick={toggleNavMode}
          >
            {navMode === "rail" ? <PanelLeft size={14} aria-hidden="true" /> : <PanelTop size={14} aria-hidden="true" />}
            <span>{navMode === "rail" ? "左侧轨" : "顶部栏"}</span>
          </button>

          {/* ⌘K Trigger */}
          <button
            type="button"
            className="topbar-cmdk-trigger"
            aria-label="打开命令面板"
            onClick={() => {
              setHealthOpen(false);
              setDrawerOpen(false);
              setCmdkQuery("");
              setCmdkOpen(true);
            }}
          >
            <Search size={13} aria-hidden="true" />
            <span>搜索或命令…</span>
            <kbd>⌘K</kbd>
          </button>

          <div className="app-topbar-actions">
            <button
              type="button"
              className={`topbar-btn topbar-state${activeTasks.length ? " is-running" : ""}`}
              title={activeTasks.length ? `${activeTasks.length} 个任务正在运行` : "当前没有运行中的任务"}
              onClick={() =>
                go(
                  activeTasks.length === 1
                    ? `/tasks/${encodeURIComponent(activeTasks[0].id)}/runtime`
                    : "/tasks",
                )
              }
            >
              <i className={activeTasks.length ? "running-dot" : "idle-dot"} aria-hidden="true" />
              <span>{activeTasks.length ? `运行中 (${activeTasks.length})` : "空闲"}</span>
            </button>

            <button
              type="button"
              className={`topbar-btn topbar-health health-${healthStatus}`}
              aria-expanded={healthOpen}
              title="查看系统健康状态"
              onClick={() => {
                setCmdkOpen(false);
                setDrawerOpen(false);
                setHealthOpen((open) => !open);
              }}
            >
              <HeartPulse size={14} aria-hidden="true" />
              <span>{healthLabel(healthStatus)}</span>
            </button>

            <button
              type="button"
              className={`topbar-btn topbar-bell${pending > 0 ? " is-alert" : ""}`}
              aria-label={`审批中心，${pending} 条待处理`}
              aria-expanded={drawerOpen}
              title="中断与审批队列"
              onClick={() => {
                setCmdkOpen(false);
                setHealthOpen(false);
                setDrawerOpen((open) => !open);
              }}
            >
              <Bell size={14} aria-hidden="true" />
              <span>{pending > 0 ? `中断 ${pending}` : "中断"}</span>
              {pending > 0 ? <i className="topbar-notification-dot" aria-hidden="true" /> : null}
            </button>
          </div>
        </header>

        <main className={`app-main app-page${isRuntimePage(route.page) ? " runtime-main" : ""}`}>
          {children}
        </main>
      </div>

      {/* Backdrop for Overlays */}
      {cmdkOpen || drawerOpen ? (
        <div
          className="shell-backdrop"
          role="presentation"
          onClick={() => {
            setCmdkOpen(false);
            setDrawerOpen(false);
          }}
        />
      ) : null}

      {/* Global ⌘K Command Palette */}
      {cmdkOpen ? (
        <div className="cmdk-dialog" role="dialog" aria-modal="true" aria-label="全局命令面板">
          <div className="cmdk-input-bar">
            <Command size={15} className="cmdk-accent" aria-hidden="true" />
            <input
              ref={cmdkInputRef}
              value={cmdkQuery}
              onChange={(event) => setCmdkQuery(event.target.value)}
              placeholder="跳转任务、页面、设置或切换主题…"
              onKeyDown={(event) => {
                if (event.key === "Enter" && commandItems[0]) {
                  event.preventDefault();
                  commandItems[0].run();
                }
              }}
            />
            <kbd>ESC</kbd>
          </div>
          <div className="cmdk-results">
            {commandItems.length ? (
              commandItems.map((item) => (
                <button key={item.id} type="button" className="cmdk-row" onClick={item.run}>
                  <span className="cmdk-row-main">
                    <em className="cmdk-group">{item.group}</em>
                    <span>{item.label}</span>
                  </span>
                  <small className="cmdk-meta">{item.meta}</small>
                </button>
              ))
            ) : (
              <p className="cmdk-empty">没有匹配的指令或任务</p>
            )}
          </div>
        </div>
      ) : null}

      {/* Topbar Health Popover */}
      {healthOpen ? (
        <section className="health-popover" role="dialog" aria-label="系统实时健康">
          <header className="popover-head">
            <span>SYSTEM TELEMETRY</span>
            <button type="button" className="text-button" onClick={() => void health.refetch()}>
              <RefreshCw size={12} /> 刷新
            </button>
          </header>
          <ul className="popover-list">
            {components.length ? (
              components.map((item) => (
                <li key={item.id}>
                  <span className="popover-item-left">
                    <i className={`status-dot tone-${item.status === "unavailable" ? "err" : item.status === "degraded" ? "warn" : "ok"}`} />
                    <strong>{item.label}</strong>
                  </span>
                  <span className="popover-item-right">
                    <small>{item.latencyMs !== null ? `${item.latencyMs}ms` : item.detail}</small>
                  </span>
                </li>
              ))
            ) : (
              <li className="popover-empty">正在探测组件健康…</li>
            )}
          </ul>
          <footer className="popover-foot">
            <button type="button" className="ref-secondary-button" onClick={() => go("/system")}>
              完整系统诊断 →
            </button>
          </footer>
        </section>
      ) : null}

      {/* Right Slide-out Interrupt Drawer */}
      <aside className={`interrupt-drawer${drawerOpen ? " is-open" : ""}`} aria-label="中断处理抽屉" aria-hidden={!drawerOpen}>
        <header className="drawer-head">
          <div>
            <strong>INTERRUPT QUEUE</strong>
            <small>{pending} 条待处理事项</small>
          </div>
          <div className="drawer-head-actions">
            <button type="button" className="text-button" onClick={() => void attention.refetch()} title="刷新中断列表">
              <RefreshCw size={13} />
            </button>
            <button type="button" className="text-button" aria-label="关闭中断抽屉" onClick={() => setDrawerOpen(false)}>
              <X size={15} />
            </button>
          </div>
        </header>
        {drawerError ? <p className="inline-error drawer-error" role="alert">{drawerError}</p> : null}
        <div className="drawer-body">
          {attentionItems.length ? (
            attentionItems.map((item) => (
              <article key={item.id} className={`drawer-item kind-${item.kind}`}>
                <header>
                  <span className={`ref-chip ${item.kind === "agent_paused" ? "tone-warn" : "tone-danger"}`}>
                    {item.kind === "agent_paused" ? <Bot size={12} /> : <AlertTriangle size={12} />}
                    {item.kind === "agent_paused" ? "暂停" : "失败"}
                  </span>
                  <time>{formatShort(item.created_at)}</time>
                </header>
                <h3>{item.title}</h3>
                <p className="drawer-item-task">
                  {item.task_title}
                  {item.agent_id ? ` · ${item.agent_id}` : ""}
                </p>
                <p className="drawer-item-detail">{item.detail}</p>
                <footer>
                  {item.kind === "agent_paused" && item.agent_id ? (
                    <button
                      type="button"
                      className="ref-primary-button"
                      disabled={resumingId === item.id}
                      onClick={() => void resumeAttention(item)}
                    >
                      <Play size={13} /> 恢复 Agent
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="ref-secondary-button"
                    onClick={() => go(`/tasks/${encodeURIComponent(item.task_id)}/runtime`)}
                  >
                    <ExternalLink size={13} /> 进入作战室
                  </button>
                </footer>
              </article>
            ))
          ) : (
            <div className="drawer-empty">
              <p>当前无待处理中断</p>
              <small>Agent 暂停或任务异常时会实时推送到这里。</small>
            </div>
          )}
        </div>
        <footer className="drawer-foot">
          <button type="button" className="ref-secondary-button" onClick={() => go("/approvals")}>
            打开完整审批页 →
          </button>
        </footer>
      </aside>
    </div>
  );
}

type OverallHealth = "loading" | "healthy" | "degraded" | "unavailable";
function overallHealth(
  statuses: Array<"healthy" | "available" | "degraded" | "unavailable"> | undefined,
  loading: boolean,
  failed: boolean,
): OverallHealth {
  if (failed) return "unavailable";
  if (loading || !statuses?.length) return "loading";
  if (statuses.includes("unavailable")) return "unavailable";
  if (statuses.includes("degraded")) return "degraded";
  return "healthy";
}
function healthLabel(status: OverallHealth) {
  return ({ loading: "检查中", healthy: "健康", degraded: "降级", unavailable: "异常" } as const)[status];
}
function formatShort(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
