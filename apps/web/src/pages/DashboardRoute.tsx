import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchSystemHealth } from "../api/catalog-query-adapter";
import { fetchDashboard } from "../api/operations-query-adapter";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { DashboardPage } from "./DashboardPage";

export function DashboardRoute() {
  const navigate = useNavigate();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: fetchDashboard });
  // Shares the system page's key.  The dashboard aggregate never probes MCP or
  // the runtime process, so the system card reads those from the same health
  // fan-out the system page uses; a failure here only softens that one card.
  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth });

  if (dashboard.isLoading) return <LoadingSkeleton label="正在读取运营摘要" rows={8} />;
  if (dashboard.isError || !dashboard.data) return <ErrorState
    title="运营 Dashboard 加载失败"
    description={dashboard.error instanceof Error ? dashboard.error.message : "无法读取运营聚合数据"}
    actionLabel="重试"
    onAction={() => void dashboard.refetch()}
  />;

  return <DashboardPage
    value={dashboard.data}
    health={health.data}
    onNew={() => navigate("/tasks/new")}
    onTask={(taskId) => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
    onTasks={() => navigate("/tasks")}
    onRuntime={(taskId) => navigate(`/tasks/${encodeURIComponent(taskId)}/runtime`)}
    onApprovals={(taskId) => navigate(taskId ? `/approvals?status=pending&task_id=${encodeURIComponent(taskId)}` : "/approvals?status=pending")}
    onSystem={() => navigate("/system")}
    onReports={() => navigate("/reports")}
  />;
}
