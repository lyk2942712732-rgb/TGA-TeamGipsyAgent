import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { attentionApi } from "../api/tga3-attention";
import { fetchSystemHealth } from "../api/tga3-system";
import { listTasks } from "../api/tga3-tasks";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";
import { DashboardPage } from "./DashboardPage";

export function DashboardRoute() {
  const navigate = useNavigate();
  const tasks = useQuery({ queryKey: ["tga3", "tasks"], queryFn: listTasks });
  const attention = useQuery({ queryKey: ["tga3", "attention"], queryFn: attentionApi.list });
  const health = useQuery({ queryKey: ["system", "health"], queryFn: fetchSystemHealth });

  if (tasks.isLoading || attention.isLoading) return <LoadingSkeleton label="正在读取 TGA3 状态" rows={8} />;
  if (tasks.isError || attention.isError || !tasks.data || !attention.data) return <ErrorState
    title="TGA3 Dashboard 加载失败"
    description={(tasks.error ?? attention.error) instanceof Error ? (tasks.error ?? attention.error as Error).message : "无法读取任务和待处理事项"}
    actionLabel="重试"
    onAction={() => { void tasks.refetch(); void attention.refetch(); }}
  />;

  return <DashboardPage
    tasks={tasks.data}
    attention={attention.data}
    health={health.data}
    onNew={() => navigate("/tasks/new")}
    onTask={(taskId) => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
    onTasks={() => navigate("/tasks")}
    onApprovals={() => navigate("/approvals")}
    onSystem={() => navigate("/system")}
    onReports={() => navigate("/reports")}
  />;
}
