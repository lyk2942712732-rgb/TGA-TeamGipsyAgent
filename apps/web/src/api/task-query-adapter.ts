import { fetchTasks, type TaskListItem } from "./tasks";

export type TaskListQuery = { query?: string; mode?: string; status?: string; needsAttention?: boolean; offset?: number; limit?: number };
export type TaskListResponse = { tasks: TaskListItem[]; offset?: number; limit?: number | null; total?: number; next_offset?: number | null };

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

export const fetchTaskList = (query: TaskListQuery = {}): Promise<TaskListResponse> => fetchTasks().then(({ tasks }) => {
  const filtered = tasks.filter((task) => (!query.query || task.name.toLowerCase().includes(query.query.toLowerCase())) && (!query.mode || task.mode === query.mode) && (!query.status || task.status === query.status) && (query.needsAttention === undefined || Boolean(task.needs_attention) === query.needsAttention));
  const offset = query.offset ?? 0; const limit = query.limit ?? filtered.length;
  return { tasks: filtered.slice(offset, offset + limit), offset, limit, total: filtered.length, next_offset: offset + limit < filtered.length ? offset + limit : null };
});
