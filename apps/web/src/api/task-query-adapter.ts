import { requestJson } from "./client";
import type { ExecutionPolicy, ModeConfig, TaskListItem } from "./tasks";
import type { RuntimeEvent } from "../features/runtime/models/types";

export type TaskListQuery = {
  query?: string;
  mode?: string;
  status?: string;
  needsAttention?: boolean;
  offset?: number;
  limit?: number;
};

export type TaskListResponse = {
  tasks: TaskListItem[];
  offset?: number;
  limit?: number | null;
  total?: number;
  next_offset?: number | null;
};

export type TaskDetail = {
  schema_version: number;
  task_id: string;
  task: {
    id: string;
    name: string;
    mode: string;
    goal: string;
    task_entry_url?: string | null;
    schema_version: number;
  };
  task_spec: {
    task_id: string;
    objective: string;
    instructions: Array<Record<string, unknown>>;
    constraints: Array<Record<string, unknown>>;
    success_criteria: Array<Record<string, unknown>>;
    resources: Array<Record<string, unknown>>;
    legacy_import?: boolean;
    provenance?: Record<string, unknown>;
  };
  lifecycle: TaskLifecycle;
  input_summary: {
    prompt_present: boolean;
    prompt_preview: string;
    file_count: number;
    files: Array<Record<string, unknown>>;
    task_entry_url?: string | null;
  };
  config_snapshot: {
    mode_config: ModeConfig | Record<string, unknown>;
    execution_policy: ExecutionPolicy | Record<string, unknown>;
    execution_budget: Record<string, number>;
    model: Record<string, unknown> | null;
    mcp_capabilities: Record<string, unknown>;
    task_common_skills: Record<string, unknown> | null;
    agent_prompt: Record<string, unknown> | null;
  };
};

export type TaskLifecycle = {
  created_at: string;
  updated_at: string;
  status: string;
  turn_count: number;
  max_turns: number;
  started_at?: string | null;
  finished_at?: string | null;
  stop_reason?: string;
  active_solvers: number;
  pending_approvals: number;
  intent_total: number;
  intent_completed: number;
  flags: number;
  findings: number;
  artifacts: number;
  needs_attention: boolean;
  latest_event?: { seq: number; type: string; created_at: string } | null;
};

export type TaskTeamResponse = {
  task_id: string;
  team: Record<string, unknown>;
  solvers: Array<Record<string, unknown>>;
};

export type TaskEvidenceResponse = {
  task_id: string;
  artifacts: Page<Record<string, unknown>>;
  evidence_claims: Page<Record<string, unknown>>;
  findings: Page<Record<string, unknown>>;
};

export type Page<T> = { offset: number; limit: number; total: number; next_offset?: number | null; items: T[] };

export function taskListQueryString(query: TaskListQuery): string {
  const params = new URLSearchParams();
  if (query.query?.trim()) params.set("query", query.query.trim());
  if (query.mode) params.set("mode", query.mode);
  if (query.status) params.set("status", query.status);
  if (query.needsAttention !== undefined) params.set("needs_attention", String(query.needsAttention));
  if (query.offset) params.set("offset", String(query.offset));
  if (query.limit) params.set("limit", String(query.limit));
  return params.toString();
}

export const fetchTaskList = (query: TaskListQuery = {}) => {
  const suffix = taskListQueryString(query);
  return requestJson<TaskListResponse>(`/api/v2/tasks${suffix ? `?${suffix}` : ""}`);
};

export const fetchTaskDetail = (taskId: string) => requestJson<TaskDetail>(`/api/v2/tasks/${encodeURIComponent(taskId)}`);
export const fetchTaskTeam = (taskId: string) => requestJson<TaskTeamResponse>(`/api/v2/tasks/${encodeURIComponent(taskId)}/team`);
export const fetchTaskInputs = (taskId: string) => requestJson<{ task_goal: string; prompt: string; files: Array<Record<string, unknown>>; task_entry_url?: string | null }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/inputs`);
export const fetchTaskEvidence = (taskId: string) => requestJson<TaskEvidenceResponse>(`/api/v2/tasks/${encodeURIComponent(taskId)}/evidence?offset=0&limit=100`);
export const fetchTaskHistory = (taskId: string) => requestJson<{ events: RuntimeEvent[]; latest_seq: number; has_more: boolean }>(`/api/v2/tasks/${encodeURIComponent(taskId)}/timeline?after_seq=0&limit=200`);
