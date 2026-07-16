import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchTasks, type TaskListItem } from "../api/tasks";
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
  const [collapsed, setCollapsed] = useState(false);
  const refreshTasks = async () => { try { setTasks((await fetchTasks()).tasks); } catch { setTasks([]); } };
  useEffect(() => { void refreshTasks(); }, []);
  const go = (path: string) => navigate(path);
  return <div className={`console-shell ${collapsed ? "nav-collapsed" : ""}`}>
    <aside className="app-nav" aria-label="主导航"><div className="brand-row"><button className="icon-button" title="折叠导航" onClick={() => setCollapsed((value) => !value)}>{collapsed ? "›" : "‹"}</button><div className="brand-copy"><strong>TGA</strong><span>Trusted Goal Agent</span></div></div>
      <nav><Nav active={route.page === "dashboard"} icon="▦" label="总览" collapsed={collapsed} onClick={() => go("/")} /><Nav active={route.page === "new"} icon="＋" label="新建 Session" collapsed={collapsed} onClick={() => go("/tasks/new")} /><div className="nav-caption">任务</div>{tasks.slice(0, 7).map((task) => <Nav key={task.task_id} active={route.taskId === task.task_id} icon={task.status === "running" ? "●" : "○"} label={task.name || task.task_id} collapsed={collapsed} onClick={() => go(`/tasks/${encodeURIComponent(task.task_id)}/runtime`)} />)}<div className="nav-caption">配置</div><Nav active={route.page === "models"} icon="◈" label="模型" collapsed={collapsed} onClick={() => go("/settings/models")} /><Nav active={route.page === "capabilities"} icon="⌘" label="能力与 MCP" collapsed={collapsed} onClick={() => go("/settings/capabilities")} /><Nav active={route.page === "skills"} icon="◇" label="Skills" collapsed={collapsed} onClick={() => go("/settings/skills")} /></nav>
      <button className="nav-refresh" title="刷新任务列表" onClick={() => void refreshTasks()}>↻{!collapsed ? " 刷新任务" : ""}</button></aside>
    <main className="app-main">{route.page === "dashboard" ? <DashboardPage tasks={tasks} onNew={() => go("/tasks/new")} onOpen={(id) => go(`/tasks/${encodeURIComponent(id)}/runtime`)} /> : null}{route.page === "new" ? <NewTaskPage onCreated={(id) => { void refreshTasks(); go(`/tasks/${encodeURIComponent(id)}/runtime`); }} /> : null}{route.page === "runtime" && route.taskId ? <SessionRuntimePage taskId={route.taskId} mode="runtime" onReplay={() => go(`/tasks/${encodeURIComponent(route.taskId!)}/replay`)} /> : null}{route.page === "replay" && route.taskId ? <SessionRuntimePage taskId={route.taskId} mode="replay" onReplay={() => undefined} /> : null}{route.page === "models" ? <ModelsPage /> : null}{route.page === "capabilities" ? <CapabilitiesPage /> : null}{route.page === "skills" ? <SkillsPage /> : null}</main>
  </div>;
}

function Nav({ active, icon, label, collapsed, onClick }: { active: boolean; icon: string; label: string; collapsed: boolean; onClick: () => void }) { return <button className={`nav-link ${active ? "active" : ""}`} title={label} onClick={onClick}><span>{icon}</span>{!collapsed ? <b>{label}</b> : null}</button>; }
