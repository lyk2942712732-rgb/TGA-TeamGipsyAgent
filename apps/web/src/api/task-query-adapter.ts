import { requestJson } from "./client";
import { fetchTasks, type ExecutionPolicy, type ModeConfig, type TaskListItem } from "./tasks";
import { loadTGA3Runtime } from "../runtime/tga3-runtime";
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
  return fetchTasks().then(({ tasks }) => {
    const filtered = tasks.filter((task) => (!query.query || task.name.toLowerCase().includes(query.query.toLowerCase())) && (!query.mode || task.mode === query.mode) && (!query.status || task.status === query.status) && (query.needsAttention === undefined || Boolean(task.needs_attention) === query.needsAttention));
    const offset = query.offset ?? 0; const limit = query.limit ?? filtered.length;
    return { tasks: filtered.slice(offset, offset + limit), offset, limit, total: filtered.length, next_offset: offset + limit < filtered.length ? offset + limit : null };
  });
};

export async function fetchTaskDetail(taskId: string): Promise<TaskDetail> { const store = await loadTGA3Runtime(taskId); const raw = store.task.raw as Record<string, unknown>; return { schema_version: 3, task_id: taskId, task: { id: taskId, name: store.task.name, mode: store.task.mode, goal: store.task.goal, schema_version: 3 }, task_spec: { task_id: taskId, objective: store.task.goal, instructions: [], constraints: [], success_criteria: [], resources: [] }, lifecycle: { created_at: String(raw.created_at ?? ""), updated_at: String(raw.updated_at ?? ""), status: store.session.status, turn_count: 0, max_turns: 0, active_solvers: store.session.activeSolverCount, pending_approvals: 0, intent_total: 0, intent_completed: 0, flags: store.modeProjection.flags.length, findings: Object.keys(store.findingsById).length, artifacts: 0, needs_attention: ["waiting_user", "failed"].includes(store.session.status), latest_event: store.latestSeq ? { seq: store.latestSeq, type: store.eventsBySeq[store.latestSeq]?.type ?? "UPDATED", created_at: store.eventsBySeq[store.latestSeq]?.createdAt ?? String(raw.updated_at ?? "") } : null }, input_summary: { prompt_present: Boolean(store.task.goal), prompt_preview: store.task.goal, file_count: 0, files: [] }, config_snapshot: { mode_config: { mode: store.task.mode } as ModeConfig, execution_policy: {}, execution_budget: {}, model: null, mcp_capabilities: {}, agent_prompt: null } }; }
export async function fetchTaskTeam(taskId: string): Promise<TaskTeamResponse> { const store = await loadTGA3Runtime(taskId); return { task_id: taskId, team: store.team, solvers: Object.values(store.solversById) }; }
export async function fetchTaskInputs(taskId: string) { const store = await loadTGA3Runtime(taskId); return { task_goal: store.task.goal, prompt: store.task.goal, files: [] }; }
export async function fetchTaskEvidence(taskId: string): Promise<TaskEvidenceResponse> { const store = await loadTGA3Runtime(taskId); return { task_id: taskId, artifacts: { offset: 0, limit: 100, total: 0, items: [] }, evidence_claims: { offset: 0, limit: 100, total: 0, items: [] }, findings: { offset: 0, limit: 100, total: Object.keys(store.findingsById).length, items: Object.values(store.findingsById) } }; }
export async function fetchTaskHistory(taskId: string): Promise<{ events: RuntimeEvent[]; latest_seq: number; has_more: boolean }> { const store = await loadTGA3Runtime(taskId); return { events: Object.values(store.eventsBySeq), latest_seq: store.latestSeq, has_more: false }; }
