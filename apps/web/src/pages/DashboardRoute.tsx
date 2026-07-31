import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDashboard } from "../api/operations-query-adapter";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { DashboardPage } from "./DashboardPage";

export function DashboardRoute() {
  const navigate = useNavigate();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: fetchDashboard });

  if (dashboard.isLoading) return <LoadingSkeleton label="正在读取运营摘要" rows={8} />;
  if (dashboard.isError || !dashboard.data) return <ErrorState
    title="运营 Dashboard 加载失败"
    description={dashboard.error instanceof Error ? dashboard.error.message : "无法读取运营聚合数据"}
    actionLabel="重试"
    onAction={() => void dashboard.refetch()}
  />;

  return <DashboardPage
    value={dashboard.data}
    onNew={() => navigate("/tasks/new")}
    onTask={(taskId) => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
    onRuntime={(taskId) => navigate(`/tasks/${encodeURIComponent(taskId)}/runtime`)}
    onApprovals={(taskId) => navigate(taskId ? `/approvals?status=pending&task_id=${encodeURIComponent(taskId)}` : "/approvals?status=pending")}
  />;
}
