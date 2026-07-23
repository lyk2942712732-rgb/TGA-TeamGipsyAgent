import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { deleteTask, fetchTasks, getLLMSettings, type TaskListItem } from "../api/tasks";
import { MODE_PROFILES, TASK_MODES, type TaskMode } from "../modes";
import { SessionRuntimePage } from "../pages/SessionRuntimePage";
import { DashboardPage } from "../pages/DashboardPage";
import { NewTaskPage } from "../pages/NewTaskPage";
import { CapabilitiesPage, ModelsPage, SkillsPage } from "../pages/SettingsPages";
import { readRoute } from "./router";

export function RuntimeApp() {
  const location = useLocation();
  const navigate = useNavigate();
  const route = readRoute(location.pathname);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [llmConfigured, setLLMConfigured] = useState<boolean | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [closedScenes, setClosedScenes] = useState<Set<TaskMode>>(() => new Set());
  const grouped = useMemo(() => Object.fromEntries(TASK_MODES.map((mode) => [mode, tasks.filter((task) => task.mode === mode)])) as Record<TaskMode, TaskListItem[]>, [tasks]);
  const refreshTasks = async () => { try { setTasks((await fetchTasks()).tasks); } catch { setTasks([]); } };
  useEffect(() => { void refreshTasks(); }, []);
  useEffect(() => { void getLLMSettings().then((settings) => setLLMConfigured(settings.configured)).catch(() => setLLMConfigured(null)); }, [location.pathname]);
  const go = (path: string) => navigate(path);
  const removeTask = async (taskId: string) => { await deleteTask(taskId); if (route.taskId === taskId) go("/"); await refreshTasks(); };
  const toggleScene = (mode: TaskMode) => setClosedScenes((current) => {
    const next = new Set(current);
    if (next.has(mode)) next.delete(mode); else next.add(mode);
    return next;
  });

  return <div className={`console-shell ${collapsed ? "nav-collapsed" : ""}`}>
    <aside className="app-nav" aria-label="主导航">
      <div className="brand-row"><button className="icon-button" title="折叠导航" onClick={() => setCollapsed((value) => !value)}>{collapsed ? "›" : "‹"}</button><div className="brand-copy"><strong>TGA</strong><span>Trusted Goal Agent</span></div></div>
      <nav>
        <Nav active={route.page === "dashboard"} icon="▦" label="总览" collapsed={collapsed} onClick={() => go("/")} />
        <Nav active={route.page === "new"} icon="＋" label="新建任务" collapsed={collapsed} onClick={() => go("/tasks/new")} />
        {!collapsed ? <div className="nav-caption">场景任务</div> : null}
        {!collapsed ? <div className="scene-task-groups">{TASK_MODES.map((mode) => {
          const sceneTasks = grouped[mode];
          const closed = closedScenes.has(mode);
          return <section className="scene-task-group" key={mode}>
            <button className="scene-group-toggle" aria-expanded={!closed} onClick={() => toggleScene(mode)}><span>{closed ? "›" : "⌄"}</span><b>{MODE_PROFILES[mode].label}</b><em>{sceneTasks.length}</em></button>
            {!closed ? <div className="scene-task-list">{sceneTasks.length ? sceneTasks.map((task) => <button key={task.task_id} className={`scene-task-link ${route.taskId === task.task_id ? "active" : ""}`} title={task.name || task.task_id} onClick={() => go(`/tasks/${encodeURIComponent(task.task_id)}/runtime`)}><i className={task.status} /><span>{task.name || task.task_id}</span></button>) : <small>暂无任务</small>}</div> : null}
          </section>;
        })}</div> : null}
        {!collapsed ? <div className="nav-caption">配置</div> : null}
        <Nav active={route.page === "models"} icon="◈" label="Provider 与模型" collapsed={collapsed} onClick={() => go("/settings/models")} />
        <Nav active={route.page === "capabilities"} icon="⌘" label="能力与 MCP" collapsed={collapsed} onClick={() => go("/settings/capabilities")} />
        <Nav active={route.page === "skills"} icon="◇" label="Skills 与 Prompts" collapsed={collapsed} onClick={() => go("/settings/skills")} />
      </nav>
      <button className="nav-refresh" title="刷新任务列表" onClick={() => void refreshTasks()}>↻{!collapsed ? " 刷新任务" : ""}</button>
    </aside>
    <main className={`app-main ${route.page === "runtime" || route.page === "replay" ? "runtime-main" : ""}`}>
      {llmConfigured === false && route.page !== "models" ? <div className="model-config-banner" role="alert"><div><strong>尚未配置模型</strong><span>Agent 任务需要模型才能启动或恢复，请先完成 Provider 配置。</span></div><button onClick={() => go("/settings/models")}>去配置模型</button></div> : null}
      {route.page === "dashboard" ? <DashboardPage tasks={tasks} onNew={() => go("/tasks/new")} onOpen={(id) => go(`/tasks/${encodeURIComponent(id)}/runtime`)} onDelete={removeTask} /> : null}
      {route.page === "new" ? <NewTaskPage onCreated={(id) => { void refreshTasks(); go(`/tasks/${encodeURIComponent(id)}/runtime`); }} /> : null}
      {route.page === "runtime" && route.taskId ? <SessionRuntimePage taskId={route.taskId} mode="runtime" onReplay={() => go(`/tasks/${encodeURIComponent(route.taskId!)}/replay`)} /> : null}
      {route.page === "replay" && route.taskId ? <SessionRuntimePage taskId={route.taskId} mode="replay" onReplay={() => undefined} /> : null}
      {route.page === "models" ? <ModelsPage /> : null}
      {route.page === "capabilities" ? <CapabilitiesPage /> : null}
      {route.page === "skills" ? <SkillsPage /> : null}
    </main>
  </div>;
}

function Nav({ active, icon, label, collapsed, onClick }: { active: boolean; icon: string; label: string; collapsed: boolean; onClick: () => void }) { return <button className={`nav-link ${active ? "active" : ""}`} title={label} onClick={onClick}><span>{icon}</span>{!collapsed ? <b>{label}</b> : null}</button>; }
