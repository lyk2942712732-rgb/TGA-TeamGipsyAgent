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
  schema_version: 1;
  generated_at: string;
  metrics: {
    running_tasks: number | null;
    pending_approvals: number | null;
    awaiting_user_input: number | null;
    blocked_tasks: number | null;
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
  schema_version: 1;
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

export const fetchDashboard = () => requestJson<DashboardResponse>("/api/v2/dashboard");

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
