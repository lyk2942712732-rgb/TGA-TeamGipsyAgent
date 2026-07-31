import { useLocation, useNavigate } from "react-router-dom";
import { TaskRuntimePage } from "../features/runtime/TaskRuntimePage";
import { ApprovalsPage } from "../pages/ApprovalsPage";
import { DashboardRoute } from "../pages/DashboardRoute";
import { NewTaskPage } from "../pages/NewTaskPage";
import { ModelsPage } from "../pages/ModelsPage";
import { ProductCatalogPage } from "../pages/ProductCatalogPage";
import { SkillsPage } from "../pages/SkillsPage";
import { SystemPage } from "../pages/SystemPage";
import { CapabilitiesPage } from "../pages/ToolsPage";
import { TaskDetailPage } from "../pages/TaskDetailPage";
import { TaskListPage } from "../pages/TaskListPage";
import { AppShell } from "./AppShell";
import { readRoute, type AppRoute } from "./router";

export function RuntimeApp() {
  const location = useLocation();
  const navigate = useNavigate();
  const route = readRoute(location.pathname);

  return <AppShell route={route}>
    <RoutePage route={route} navigate={navigate} />
  </AppShell>;
}

function RoutePage({ route, navigate }: { route: AppRoute; navigate: (path: string) => void }) {
  if (route.page === "dashboard") return <DashboardRoute />;
  if (route.page === "tasks") return <TaskListPage />;
  if (route.page === "approvals") return <ApprovalsPage />;
  if (route.page === "new") return <NewTaskPage onCreated={(id) => navigate(`/tasks/${encodeURIComponent(id)}/runtime`)} />;
  if (route.page === "task-detail" && route.taskId) return <TaskDetailPage taskId={route.taskId} />;
  if (route.page === "runtime" && route.taskId) return <TaskRuntimePage taskId={route.taskId} mode="runtime" />;
  if (route.page === "replay" && route.taskId) return <TaskRuntimePage taskId={route.taskId} mode="replay" />;
  if (route.page === "models") return <ModelsPage />;
  if (route.page === "tools") return <CapabilitiesPage />;
  if (route.page === "skills") return <SkillsPage />;
  if (route.page === "resources") return <ProductCatalogPage kind="resources" />;
  if (route.page === "reports") return <ProductCatalogPage kind="reports" />;
  if (route.page === "knowledge-bases") return <ProductCatalogPage kind="knowledge-bases" />;
  if (route.page === "teams") return <ProductCatalogPage kind="teams" />;
  if (route.page === "solvers") return <ProductCatalogPage kind="solvers" />;
  if (route.page === "policies") return <ProductCatalogPage kind="policies" />;
  if (route.page === "system") return <SystemPage />;
  return <NotFoundPage navigate={navigate} />;
}

function NotFoundPage({ navigate }: { navigate: (path: string) => void }) {
  return <section className="page-stack route-not-found">
    <span className="eyebrow">404 / ROUTE REMOVED</span>
    <h1>此入口不存在</h1>
    <p>旧 Session 与聚合 Settings URL 已完成一次性迁移，不再提供别名或重定向。</p>
    <div className="button-row"><button onClick={() => navigate("/tasks")}>打开任务列表</button><button className="secondary-button" onClick={() => navigate("/")}>返回首页</button></div>
  </section>;
}
