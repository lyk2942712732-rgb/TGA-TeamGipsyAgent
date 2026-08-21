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

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export type GlobalApproval = {
  approval_id: string;
  task_id: string;
  task_name: string;
  solver_id: string;
  intent_id?: string | null;
  action_id: string;
  action_kind: string;
  capability: string;
  target: string;
  risk: string;
  effect: Record<string, unknown>;
  rationale: string;
  expected_outcome: string;
  alternative_analysis: string;
  alternatives: string[];
  reversibility: string;
  expires_at?: string | null;
  status: ApprovalStatus;
  decision_allowed: boolean;
  decision_block_reason?: string | null;
  created_at: string;
  updated_at: string;
};

export type ApprovalQuery = {
  status: ApprovalStatus;
  taskId?: string;
  solverId?: string;
  intentId?: string;
  risk?: string;
  capability?: string;
  deadline?: string;
  page?: number;
  limit?: number;
};

export type GlobalApprovalPage = {
  view_version: 1;
  offset: number;
  limit: number;
  total: number;
  next_offset?: number | null;
  items: GlobalApproval[];
  filters: Record<string, string | null>;
};

export function approvalQueryString(query: ApprovalQuery): string {
  const params = new URLSearchParams();
  params.set("status", query.status);
  if (query.taskId?.trim()) params.set("task_id", query.taskId.trim());
  if (query.solverId?.trim()) params.set("solver_id", query.solverId.trim());
  if (query.intentId?.trim()) params.set("intent_id", query.intentId.trim());
  if (query.risk) params.set("risk", query.risk);
  if (query.capability?.trim()) params.set("capability", query.capability.trim());
  if (query.deadline) params.set("deadline", query.deadline);
  const limit = query.limit ?? 20;
  const page = Math.max(1, query.page ?? 1);
  params.set("offset", String((page - 1) * limit));
  params.set("limit", String(limit));
  return params.toString();
}

export async function fetchDashboard(): Promise<DashboardResponse> {
  const tasks = await requestJson<Array<{ id: string; title: string; scene_id: string; state: string; updated_at: string }>>("/api/v3/tasks");
  const rows: OperationalTaskSummary[] = tasks.map((task) => ({ task_id: task.id, name: task.title, mode: task.scene_id, status: task.state, updated_at: task.updated_at, active_solvers: 0, pending_approvals: 0, intent_total: 0, intent_completed: 0, findings: 0, artifacts: 0, turn_count: 0, max_turns: 0, needs_attention: ["waiting_user", "failed"].includes(task.state) }));
  return { view_version: 1, generated_at: new Date().toISOString(), metrics: { running_tasks: rows.filter((row) => ["starting", "running", "finalizing", "reporting"].includes(row.status)).length, pending_approvals: 0, active_solvers: 0 }, needs_attention: rows.filter((row) => row.needs_attention).map((row) => ({ id: row.task_id, kind: row.status === "waiting_user" ? "user_input" : "blocked", task_id: row.task_id, task_name: row.name, title: row.status === "waiting_user" ? "等待用户回答" : "任务需要检查", description: row.status, status: row.status, updated_at: row.updated_at })), active_tasks: rows.filter((row) => !["completed", "cancelled", "failed"].includes(row.status)), recent_completed: rows.filter((row) => ["completed", "cancelled", "failed"].includes(row.status)).slice(0, 10), system_status: [{ id: "tga3", label: "TGA3 Control Plane", status: "healthy", detail: "任务列表接口可用", available: true }], unavailable_metrics: ["pending_approvals", "active_solvers"] };
}

export const fetchGlobalApprovals = (query: ApprovalQuery) => (
  requestJson<GlobalApprovalPage>(`/api/v2/approvals?${approvalQueryString(query)}`)
);

export const decideGlobalApproval = (
  approval: Pick<GlobalApproval, "task_id" | "action_id">,
  decision: "approve" | "reject",
) => requestJson<{ accepted?: boolean; status?: string; scheduled?: boolean }>(
  `/api/v2/tasks/${encodeURIComponent(approval.task_id)}/approvals/${encodeURIComponent(approval.action_id)}/decision`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  },
);
