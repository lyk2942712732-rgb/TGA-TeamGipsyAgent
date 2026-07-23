import { apiBase, requestJson } from "./client";
import type { TaskMode } from "../modes";

export type ModeConfig = { mode: TaskMode; [key: string]: unknown };
// Read-only compatibility projection for historical Session snapshots.
export type ResourceRef = { id: string; role: "target" | "hint"; kind: string; label: string; [key: string]: unknown };
export type ExecutionPolicy = {
  network: { mode: "none" | "observe" | "interact"; allowed_scopes: string[]; rate_limit: number; concurrency: number };
  filesystem: { mode: "read_only" | "workspace_write"; allowed_roots: string[] };
  process_execution: { mode: "forbidden" | "sandbox_only" | "authorized_host"; timeout_seconds: number };
  fuzzing: { mode: "disabled" | "bounded" | "extended"; max_cases: number; max_duration_seconds: number; concurrency: number };
  state_change: { mode: "forbidden" | "approval_required" | "authorized"; allowed_actions: string[] };
  containment: { mode: "observe_only" | "approval_required" | "authorized"; allowed_actions: string[] };
  source: "default" | "user" | "legacy_migration";
};

export type ModeProfileContract = {
  id: TaskMode; label: string; description: string; default_goal: string;
  default_mode_config: ModeConfig; default_execution_policy: ExecutionPolicy;
  allowed_input_kinds: string[]; required_conditions: string[];
  recommended_capabilities: string[]; prompt_instruction: string;
  completion_validator: string; report_sections: string[]; uses_flag: boolean;
  advanced_settings: string[]; mode_config_schema: Record<string, unknown>; execution_policy_schema: Record<string, unknown>;
};

export type StagedAsset = {
  id: string;
  originalName: string;
  mimeType: string;
  mediaKind: "image" | "text" | "document" | "archive" | "binary" | "other";
  size: number;
  sha256: string;
  status: "uploading" | "uploaded" | "failed";
  previewUrl?: string;
  error?: string;
};

export type CreateSessionRequest = {
  id: string;
  name: string;
  mode: TaskMode;
  goal: string;
  modeOptions: ModeConfig;
  input: { taskFileIds: string[]; hintText?: string; hintFileIds: string[] };
  executionPolicy: ExecutionPolicy;
};

export type TaskListItem = {
  schema_version?: number; task_id: string; name: string; mode: TaskMode; target: string;
  target_summary?: string; target_count?: number; hint_count?: number; created_at: string;
  updated_at?: string; status: string; turn_count?: number; max_turns?: number;
  active_solvers?: number; latest_event?: { seq?: number; type?: string } | null;
  flags: number; findings: number; artifacts: number;
};

export type LLMSettings = { configured: boolean; base_url: string; model: string; api_key_set: boolean; supports_vision?: boolean | null };

export const createTask = (request: CreateSessionRequest) => requestJson<{
  task_id: string; status: string; scheduled: boolean;
  mcp_capabilities: { server_ids: string[]; tools: unknown[] };
}>("/api/v2/tasks", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
});

export const fetchModeProfiles = () => requestJson<{ schema_version: number; profiles: ModeProfileContract[] }>("/api/v2/mode-profiles");

function uploadError(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  }
  return `Upload failed (${status})`;
}

export async function stageInput(file: File): Promise<StagedAsset> {
  const response = await fetch(`${apiBase}/api/v2/input-uploads?filename=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
  });
  const payload = await response.json().catch(() => ({})) as { asset?: StagedAsset };
  if (!response.ok || !payload.asset) throw new Error(uploadError(payload, response.status));
  return payload.asset;
}

export const deleteStagedInput = (assetId: string) => requestJson<{ asset_id: string; deleted: boolean }>(`/api/v2/input-uploads/${encodeURIComponent(assetId)}`, { method: "DELETE" });
export const fetchTasks = () => requestJson<{ tasks: TaskListItem[] }>("/api/v2/tasks");
export const deleteTask = (taskId: string) => requestJson<{ task_id: string; deleted: boolean }>(`/api/v2/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
export const getLLMSettings = () => requestJson<LLMSettings>("/api/v2/settings/llm");
export const updateLLMSettings = (payload: { base_url: string; api_key: string; model: string }) => requestJson<LLMSettings>("/api/v2/settings/llm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const verifyLLMSettings = () => requestJson<{ configured: boolean; reachable: boolean; action_tools: boolean; model: string }>("/api/v2/settings/llm/verify", { method: "POST" });
export type SkillSetting = { name: string; modes: TaskMode[]; capabilities: string[]; tags: string[]; version: string; source: "builtin" | "custom"; summary: string; editable: boolean };
export type SkillDetail = SkillSetting & { body: string };
export type PromptSetting = { id: string; role: string; instruction: string; source: string; editable: boolean };
export const fetchSkillSettings = () => requestJson<{ schema_version: number; skills: SkillSetting[] }>("/api/v2/settings/skills");
export const fetchSkillDetail = (name: string) => requestJson<{ skill: SkillDetail }>(`/api/v2/settings/skills/${encodeURIComponent(name)}`);
export async function importSkill(file: File, scene?: TaskMode): Promise<{ skill: SkillDetail }> {
  const response = await fetch(`${apiBase}/api/v2/settings/skills/import`, {
    method: "POST",
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "X-TGA-Filename": encodeURIComponent(file.name),
      ...(scene ? { "X-TGA-Scene": scene } : {}),
    },
    body: file,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(uploadError(payload, response.status));
  return payload as { skill: SkillDetail };
}
export const updateSkill = (name: string, payload: Pick<SkillDetail, "modes" | "capabilities" | "tags" | "version" | "body">) => requestJson<{ skill: SkillDetail }>(`/api/v2/settings/skills/${encodeURIComponent(name)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const deleteSkill = (name: string) => requestJson<{ name: string; deleted: boolean }>(`/api/v2/settings/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
export const fetchPromptSettings = () => requestJson<{ schema_version: number; prompts: PromptSetting[] }>("/api/v2/settings/prompts");
