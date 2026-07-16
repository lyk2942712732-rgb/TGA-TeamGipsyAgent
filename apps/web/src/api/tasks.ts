import { requestJson } from "./client";

export type TGATask = {
  id: string;
  name: string;
  mode: "ctf" | "web_audit" | "code_audit" | "binary_ctf";
  target: string;
  scope: string[];
  target_theme?: string;
  target_description?: string;
  intensity: "passive" | "normal" | "active";
  allow_active_scan: boolean;
  goal: string;
  flag_format?: string | null;
};

export type TaskListItem = {
  task_id: string;
  name: string;
  mode: string;
  target: string;
  created_at: string;
  status: string;
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
export const getLLMSettings = () => requestJson<LLMSettings>("/api/v2/settings/llm");
export const updateLLMSettings = (payload: { base_url: string; api_key: string; model: string }) => requestJson<LLMSettings>("/api/v2/settings/llm", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
