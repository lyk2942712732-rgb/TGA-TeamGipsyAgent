import { useLocation, useNavigate } from "react-router-dom";
import { ToastProvider } from "../components/ui/Toast";
import { TaskRuntimePage } from "../features/runtime/TaskRuntimePage";
import { ApprovalsPage } from "../pages/ApprovalsPage";
import { DashboardRoute } from "../pages/DashboardRoute";
import { NewTaskPage } from "../pages/NewTaskPage";
import { ModelsPage } from "../pages/ModelsPage";
import { PoliciesPage } from "../pages/PoliciesPage";
import { ReportsPage } from "../pages/ReportsPage";
import { SkillsPage } from "../pages/SkillsPage";
import { SolversPage } from "../pages/SolversPage";
import { SystemPage } from "../pages/SystemPage";
import { TaskListPage } from "../pages/TaskListPage";
import { AppShell } from "./AppShell";
import { readRoute, type AppRoute } from "./router";

export function RuntimeApp() {
  const location = useLocation();
  const navigate = useNavigate();
  const route = readRoute(location.pathname);

  return <ToastProvider>
    <AppShell route={route}>
      <RoutePage route={route} navigate={navigate} />
    </AppShell>
  </ToastProvider>;
}

function RoutePage({ route, navigate }: { route: AppRoute; navigate: (path: string) => void }) {
  if (route.page === "dashboard") return <DashboardRoute />;
  if (route.page === "tasks") return <TaskListPage />;
  if (route.page === "approvals") return <ApprovalsPage />;
  if (route.page === "new") return <><TaskListPage /><NewTaskPage onCancel={() => navigate("/tasks")} onCreated={(id) => navigate(`/tasks/${encodeURIComponent(id)}/runtime`)} /></>;
  if (route.page === "runtime" && route.taskId) return <TaskRuntimePage taskId={route.taskId} />;
  if (route.page === "models") return <ModelsPage />;
  if (route.page === "skills") return <SkillsPage />;
  if (route.page === "reports") return <ReportsPage />;
  if (route.page === "solvers") return <SolversPage />;
  if (route.page === "policies") return <PoliciesPage />;
  if (route.page === "system") return <SystemPage />;
  return <NotFoundPage navigate={navigate} />;
}

function NotFoundPage({ navigate }: { navigate: (path: string) => void }) {
  return <section className="page-stack route-not-found">
    <span className="eyebrow">404 / ROUTE REMOVED</span>
    <h1>此入口不存在</h1>
    <p>该地址不存在，或对应功能已从当前版本移除。</p>
    <div className="button-row"><button onClick={() => navigate("/tasks")}>打开任务列表</button><button className="secondary-button" onClick={() => navigate("/")}>返回首页</button></div>
  </section>;
}
