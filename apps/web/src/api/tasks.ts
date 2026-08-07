import { apiBase, requestJson } from "./client";
import type { TaskMode } from "../modes";

export type ModeConfig = { mode: TaskMode; [key: string]: unknown };
export type ExecutionPolicy = {
  preset: "autonomous_ctf" | "safe_observation" | "offline_analysis" | "custom";
  network: {
    access: "disabled" | "task_sources" | "public_internet" | "custom";
    interaction: "observe" | "interact";
    seed_origins: string[];
    custom_origins: string[];
    custom_domains: string[];
    custom_cidrs: string[];
    deny_private_networks: boolean;
    deny_loopback: boolean;
    deny_link_local: boolean;
    deny_cloud_metadata: boolean;
    rate_limit_per_minute: number;
    concurrency: number;
    request_timeout_seconds: number;
  };
  local_compute: {
    mode: "disabled" | "isolated";
    timeout_seconds: number;
    concurrency: number;
    network_inheritance: "task_network_policy";
  };
  high_impact: {
    mode: "forbidden" | "approval_required" | "allowlisted";
    allowed_actions: string[];
  };
};

export type ModeProfileContract = {
  id: TaskMode; label: string; description: string; default_goal: string;
  default_mode_config: ModeConfig; default_execution_policy: ExecutionPolicy;
  allowed_input_kinds: string[]; required_conditions: string[];
  recommended_capabilities: string[];
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
  input: { text: string; fileIds: string[] };
  executionPolicy: ExecutionPolicy;
  selectedSkills?: string[] | null;
  agentModels?: Record<string, { providerId: string; modelId: string }>;
  preflightFingerprint?: string | null;
};

export type TaskPreflight = {
  fingerprint: string;
  task_id: string;
  checks: Array<{ id: string; status: "passed"; detail: string }>;
  skill_snapshot: { selector: string; count: number; content_sha256: string };
  mcp_catalog_version: string;
  model_verification_id: string;
};

export type TaskListItem = {
  schema_version?: number; task_id: string; name: string; mode: TaskMode; task_entry_url?: string | null;
  target_summary?: string; target_count?: number; hint_count?: number; created_at: string;
  updated_at?: string; status: string; turn_count?: number; max_turns?: number;
  active_solvers?: number; latest_event?: { seq?: number; type?: string } | null;
  flags: number; findings: number; artifacts: number;
  pending_approvals?: number; needs_attention?: boolean;
  intent_total?: number; intent_completed?: number;
};

export type LLMVerification = {
  status: "unverified" | "verifying" | "verified" | "failed" | "stale";
  verified_at?: string | null;
  last_error?: { code: string; message: string } | null;
  capabilities?: Record<string, boolean | null>;
};
export type LLMSettings = { configured: boolean; base_url: string; model: string; api_key_set: boolean; browser_configured?: boolean; supports_vision?: boolean | null; max_output_tokens?: number; timeout_seconds?: number; temperature?: number; reasoning_mode?: "auto" | "enabled" | "disabled"; verification_status?: LLMVerification["status"]; verification?: LLMVerification };
export type LLMSettingsUpdate = { base_url: string; model: string; api_key?: string; supports_vision?: boolean | null; max_output_tokens?: number; timeout_seconds?: number; temperature?: number; reasoning_mode?: "auto" | "enabled" | "disabled" };
export type ProviderPreset = { id: string; name: string; base_url: string };
export type ProviderAPIKey = { id: string; label: string; masked: string; selected: boolean; created_at?: string };
export type ProviderModel = {
  id: string; name: string; supports_vision?: boolean | null; max_output_tokens: number;
  timeout_seconds: number; temperature: number; reasoning_mode: "auto" | "enabled" | "disabled";
  verification_status: LLMVerification["status"]; verification: LLMVerification;
};
export type ModelProvider = {
  id: string; name: string; preset_id: string; base_url: string; models: ProviderModel[];
  api_keys: ProviderAPIKey[]; selected_api_key_id?: string | null; created_at?: string; updated_at?: string;
};
export type ProviderCatalog = { schema_version: 1; presets: ProviderPreset[]; providers: ModelProvider[] };
export type AgentModelOptions = {
  mode: TaskMode;
  agents: Array<{ id: string; role: "supervisor" | "worker" | "reviewer" | "reporter"; specialties: string[]; required: boolean }>;
  models: Array<{ provider_id: string; provider_name: string; model_id: string; model_name: string; api_key_id: string; verification_status: LLMVerification["status"]; ready: boolean }>;
};

export const createTask = (request: CreateSessionRequest) => requestJson<{
  task_id: string; status: string; scheduled: boolean;
  mcp_capabilities: { server_ids: string[]; tools: unknown[] };
}>("/api/v2/tasks", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
});

export const preflightTask = (request: CreateSessionRequest) => requestJson<TaskPreflight>("/api/v2/tasks/preflight", {
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

export async function stageInput(file: File, signal?: AbortSignal): Promise<StagedAsset> {
  const response = await fetch(`${apiBase}/api/v2/input-uploads?filename=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file, signal,
  });
  const payload = await response.json().catch(() => ({})) as { asset?: StagedAsset };
  if (!response.ok || !payload.asset) throw new Error(uploadError(payload, response.status));
  return payload.asset;
}

export const deleteStagedInput = (assetId: string) => requestJson<{ asset_id: string; deleted: boolean }>(`/api/v2/input-uploads/${encodeURIComponent(assetId)}`, { method: "DELETE" });
export const fetchTasks = () => requestJson<{ tasks: TaskListItem[] }>("/api/v2/tasks");
export const deleteTask = (taskId: string) => requestJson<{ task_id: string; deleted: boolean }>(`/api/v2/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
export const getLLMSettings = () => requestJson<LLMSettings>("/api/v2/settings/llm");
export const updateLLMSettings = (payload: LLMSettingsUpdate) => requestJson<LLMSettings>("/api/v2/settings/llm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const verifyLLMSettings = () => requestJson<{ configured: boolean; reachable: boolean; action_tools: boolean; model: string; verification_status: LLMVerification["status"]; capabilities: Record<string, boolean | null>; tool_catalog: { tool_count: number; schema_bytes: number; accepted: boolean } }>("/api/v2/settings/llm/verify", { method: "POST" });
export const fetchProviderCatalog = () => requestJson<ProviderCatalog>("/api/v2/settings/llm/providers");
export const createModelProvider = (payload: { name: string; preset_id?: string; base_url: string; model: string; api_key: string; api_key_label?: string }) => requestJson<{ provider: ModelProvider }>("/api/v2/settings/llm/providers", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const addProviderModel = (providerId: string, payload: { name: string }) => requestJson<{ model: ProviderModel }>(`/api/v2/settings/llm/providers/${encodeURIComponent(providerId)}/models`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const addProviderAPIKey = (providerId: string, payload: { api_key: string; label?: string }) => requestJson<{ api_key: ProviderAPIKey }>(`/api/v2/settings/llm/providers/${encodeURIComponent(providerId)}/api-keys`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const selectProviderAPIKey = (providerId: string, keyId: string) => requestJson<{ provider: ModelProvider }>(`/api/v2/settings/llm/providers/${encodeURIComponent(providerId)}/api-keys/${encodeURIComponent(keyId)}/selection`, { method: "PUT" });
export const verifyProviderModel = (providerId: string, modelId: string) => requestJson<{ reachable: boolean; action_tools: boolean; model: string; verification_status: LLMVerification["status"] }>(`/api/v2/settings/llm/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/verify`, { method: "POST" });
export const fetchAgentModelOptions = (mode: TaskMode) => requestJson<AgentModelOptions>(`/api/v2/settings/llm/agent-options?mode=${encodeURIComponent(mode)}`);
export type SkillSetting = { name: string; modes: TaskMode[]; capabilities: string[]; tags: string[]; version: string; source: "builtin" | "custom"; summary: string; editable: boolean };
export type SkillDetail = SkillSetting & { body: string };
export type SkillPreview = {
  selector: string;
  fingerprint: string;
  count: number;
  skills: Array<Pick<SkillSetting, "name" | "version" | "capabilities" | "tags"> & {
    origin: "builtin" | "custom";
    content_sha256: string;
    selection_reasons: string[];
  }>;
};
export type ModePromptSettings = { id: TaskMode; label: string; methodology: string[]; completion_focus: string; observer_focus: string };
export type AgentPromptSettings = { schema_version: 1; common_system_prompt: string; modes: ModePromptSettings[] };
export const fetchSkillSettings = () => requestJson<{ schema_version: number; skills: SkillSetting[] }>("/api/v2/settings/skills");
export const previewTaskSkills = (payload: { mode: TaskMode; goal: string; modeOptions: ModeConfig; prompt: string; fileNames: string[]; executionPolicy: ExecutionPolicy; selectedSkills?: string[] | null }) => requestJson<SkillPreview>("/api/v2/tasks/skill-preview", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
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
export const fetchAgentPromptSettings = () => requestJson<AgentPromptSettings>("/api/v2/settings/agent-prompts");
export const updateAgentPromptSettings = (payload: AgentPromptSettings) => requestJson<AgentPromptSettings>("/api/v2/settings/agent-prompts", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
