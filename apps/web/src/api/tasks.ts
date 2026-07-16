import { requestJson } from "./client";

export type TGATask = {
  id: string;
  name: string;
  mode: "ctf" | "web_audit" | "code_audit" | "binary_ctf";
  target: string;
  target_theme?: string;
  target_description?: string;
  goal: string;
  flag_format?: string | null;
};

export type TaskListItem = {
  schema_version?: number;
  task_id: string;
  name: string;
  mode: string;
  target: string;
  created_at: string;
  updated_at?: string;
  status: string;
  turn_count?: number;
  max_turns?: number;
  active_solvers?: number;
  latest_event?: { seq?: number; type?: string } | null;
  flags: number;
  findings: number;
  artifacts: number;
};

export type LLMSettings = {
  configured: boolean;
  base_url: string;
  model: string;
  api_key_set: boolean;
};

export const createTask = (task: TGATask, initialHint?: string) => requestJson<{ task_id: string; status: string; scheduled: boolean }>("/api/v2/tasks", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ task, initial_hint: initialHint || undefined }),
});

export const fetchTasks = () => requestJson<{ tasks: TaskListItem[] }>("/api/v2/tasks");
export const deleteTask = (taskId: string) => requestJson<{ task_id: string; deleted: boolean }>(`/api/v2/tasks/${encodeURIComponent(taskId)}`, {
  method: "DELETE",
});
export const getLLMSettings = () => requestJson<LLMSettings>("/api/v2/settings/llm");
export const updateLLMSettings = (payload: { base_url: string; api_key: string; model: string }) => requestJson<LLMSettings>("/api/v2/settings/llm", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const verifyLLMSettings = () => requestJson<{ configured: boolean; reachable: boolean; action_tools: boolean; model: string }>("/api/v2/settings/llm/verify", {
  method: "POST",
});
export type SkillSetting = { name: string; modes: string[]; capabilities: string[]; tags: string[]; version: string; source: string; summary: string };
export type PromptSetting = { id: string; role: string; instruction: string; source: string; editable: boolean };
export const fetchSkillSettings = () => requestJson<{ schema_version: number; skills: SkillSetting[] }>("/api/v2/settings/skills");
export const fetchPromptSettings = () => requestJson<{ schema_version: number; prompts: PromptSetting[] }>("/api/v2/settings/prompts");
