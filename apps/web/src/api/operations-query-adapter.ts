import { requestJson } from "./client";

export type OperationalTaskSummary = {
  task_id: string;
  name: string;
  mode: string;
  status: string;
  updated_at: string;
  active_solvers: number;
  pending_approvals: number;
  intent_total: number;
  intent_completed: number;
  findings: number;
  artifacts: number;
  turn_count: number;
  max_turns: number;
  needs_attention: boolean;
  latest_event?: { seq: number; type: string; created_at: string } | null;
};

export type DashboardResponse = {
  view_version: 1;
  generated_at: string;
  metrics: {
    running_tasks: number | null;
    pending_approvals: number | null;
    active_solvers: number | null;
  };
  needs_attention: Array<{
    id: string;
    kind: "approval" | "user_input" | "blocked";
    task_id: string;
    task_name: string;
    title: string;
    description: string;
    status: string;
    risk?: string | null;
    action_id?: string | null;
    updated_at: string;
  }>;
  active_tasks: OperationalTaskSummary[];
  recent_completed: OperationalTaskSummary[];
  system_status: Array<{
    id: string;
    label: string;
    status: "healthy" | "available" | "degraded" | "unavailable";
    detail: string;
    available: boolean;
  }>;
  unavailable_metrics: string[];
};

export async function fetchDashboard(): Promise<DashboardResponse> {
  const [tasks, attention] = await Promise.all([
    requestJson<Array<{ id: string; title: string; scene_id: string; state: string; updated_at: string }>>("/api/v3/tasks"),
    requestJson<Array<{ id: string; kind: string; task_id: string; task_title: string; title: string; detail: string; created_at: string }>>("/api/v3/attention"),
  ]);
  const rows: OperationalTaskSummary[] = tasks.map((task) => ({ task_id: task.id, name: task.title, mode: task.scene_id, status: task.state, updated_at: task.updated_at, active_solvers: 0, pending_approvals: 0, intent_total: 0, intent_completed: 0, findings: 0, artifacts: 0, turn_count: 0, max_turns: 0, needs_attention: ["waiting_user", "failed"].includes(task.state) }));
  return { view_version: 1, generated_at: new Date().toISOString(), metrics: { running_tasks: rows.filter((row) => ["starting", "running", "finalizing", "reporting"].includes(row.status)).length, pending_approvals: attention.length, active_solvers: 0 }, needs_attention: attention.map((item) => ({ id: item.id, kind: item.kind === "question" ? "user_input" : "blocked", task_id: item.task_id, task_name: item.task_title, title: item.title, description: item.detail, status: item.kind, updated_at: item.created_at })), active_tasks: rows.filter((row) => !["completed", "cancelled", "failed"].includes(row.status)), recent_completed: rows.filter((row) => ["completed", "cancelled", "failed"].includes(row.status)).slice(0, 10), system_status: [{ id: "tga3", label: "TGA3 Control Plane", status: "healthy", detail: "任务与待处理队列接口可用", available: true }], unavailable_metrics: ["active_solvers"] };
}
