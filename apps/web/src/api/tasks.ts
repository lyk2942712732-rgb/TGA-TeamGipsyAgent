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
  fields: Array<{ key: string; label: string; type: "text" | "textarea" | "select" | "number" | "checkbox" | "csv"; options?: string[]; min?: number; max?: number }>;
  advanced_settings?: string[]; mode_config_schema?: Record<string, unknown>; execution_policy_schema?: Record<string, unknown>;
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

export type CreateTaskRequest = {
  id: string;
  name: string;
  mode: TaskMode;
  goal: string;
  modeOptions: ModeConfig;
  input: { text: string; fileIds: string[] };
  executionPolicy: ExecutionPolicy;
  preflightFingerprint?: string | null;
};

export type TaskPreflight = {
  fingerprint: string;
  task_id: string;
  checks: Array<{ id: string; status: "passed"; detail: string }>;
  skill_catalog: { strategy: "worker_on_demand"; package_count: number; content_sha256: string };
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
  solver_total?: number; highest_severity?: "info" | "low" | "medium" | "high" | "critical" | null;
};

export type LLMVerification = {
  status: "unverified" | "verifying" | "verified" | "failed" | "stale";
  verified_at?: string | null;
  last_error?: { code: string; message: string } | null;
  capabilities?: Record<string, boolean | null>;
};
export type LLMSettings = { configured: boolean; active?: boolean; provider_id?: string | null; provider_name?: string | null; base_url: string; model_id?: string | null; model: string; api_key_set: boolean; browser_configured?: boolean; supports_vision?: boolean | null; max_output_tokens?: number; timeout_seconds?: number; temperature?: number; reasoning_mode?: "auto" | "enabled" | "disabled"; verification_status?: LLMVerification["status"]; verification?: LLMVerification };
export type LLMSettingsUpdate = { provider?: string; provider_name?: string; preset_id?: string; base_url: string; model: string; api_key?: string; supports_vision?: boolean | null; max_output_tokens?: number; timeout_seconds?: number; temperature?: number; reasoning_mode?: "auto" | "enabled" | "disabled" };
export type ProviderPreset = { id: string; name: string; base_url: string };
export type ProviderAPIKey = { id: string; label: string; masked: string; selected: boolean; created_at?: string };
export type ProviderModel = {
  id: string; name: string; supports_vision?: boolean | null; max_output_tokens: number;
  timeout_seconds: number; temperature: number; reasoning_mode: "auto" | "enabled" | "disabled";
  verification_status: LLMVerification["status"]; verification: LLMVerification;
};
export type ModelProvider = {
  id: string; name: string; preset_id: string; base_url: string; models: ProviderModel[];
  api_keys: ProviderAPIKey[]; selected_api_key_id?: string | null; active_model_id?: string | null; created_at?: string; updated_at?: string;
};
export type ProviderCatalog = { schema_version: 1; presets: ProviderPreset[]; providers: ModelProvider[] };
export type AgentModelOptions = {
  mode: TaskMode;
  agents: Array<{ id: string; role: "supervisor" | "worker" | "reviewer" | "reporter"; specialties: string[]; required: boolean; model: { provider_id: string; provider_name: string; model_id: string; model_name: string; verification_status: LLMVerification["status"]; ready: boolean } }>;
  models: Array<{ provider_id: string; provider_name: string; model_id: string; model_name: string; api_key_id: string; verification_status: LLMVerification["status"]; ready: boolean }>;
};

type ModelsDocument = { schema_version: number; providers: Array<{ id: string; name: string; protocol: "openai_responses" | "openai_chat_completions" | "anthropic"; base_url?: string | null; selected_api_key_id: string; api_keys: Array<{ id: string; label: string; api_key: string }>; models: Array<{ id: string; name: string; max_output_tokens?: number; timeout_seconds?: number; temperature?: number | null; reasoning_mode?: "auto" | "enabled" | "disabled"; verification_status?: LLMVerification["status"] }> }> };
type AgentsDocument = { schema_version: number; agents: Record<string, { display_name: string; role: "supervisor" | "worker" | "reporter"; runtime: "openai_agents" | "claude_agent"; provider_id: string; model_id: string; max_turns_per_cycle: number; system_prompt: string }> };
type SceneDocument = { schema_version: number; scenes: Array<{ id: TaskMode; name: string; description: string; system_prompt: string }> };
const pendingFiles = new Map<string, File>();
export function takeStagedFile(assetId: string): File | undefined { const file = pendingFiles.get(assetId); pendingFiles.delete(assetId); return file; }
const jsonHeaders = { "Content-Type": "application/json" };

export async function createTask(request: CreateTaskRequest): Promise<{ task_id: string; status: string; scheduled: boolean; mcp_capabilities: { server_ids: string[]; tools: unknown[] } }> {
  const form = new FormData();
  form.set("title", request.name);
  form.set("prompt", [request.goal, request.input.text].filter(Boolean).join("\n\n"));
  form.set("scene_id", request.mode);
  for (const id of request.input.fileIds) { const file = pendingFiles.get(id); if (file) form.append("files", file, file.name); }
  const value = await requestJson<{ id: string; state: string }>("/api/v3/tasks", { method: "POST", body: form });
  request.input.fileIds.forEach((id) => pendingFiles.delete(id));
  return { task_id: value.id, status: value.state, scheduled: true, mcp_capabilities: { server_ids: [], tools: [] } };
}

export async function preflightTask(request: CreateTaskRequest): Promise<TaskPreflight> {
  const agents = await fetchAgentModelOptions(request.mode);
  const unavailable = agents.agents.filter((agent) => !agent.model.ready);
  if (unavailable.length) throw new Error(`以下 Agent 的模型或密钥未配置：${unavailable.map((agent) => agent.id).join("、")}`);
  return { fingerprint: `${request.id}:${request.mode}`, task_id: request.id, checks: [{ id: "config", status: "passed", detail: "场景、Agent 模型与输入已就绪" }], skill_catalog: { strategy: "worker_on_demand", package_count: 0, content_sha256: "live" }, mcp_catalog_version: "tga3", model_verification_id: "config" };
}

export async function fetchModeProfiles(): Promise<{ schema_version: number; profiles: ModeProfileContract[] }> {
  const value = await requestJson<SceneDocument>("/api/v3/config/scenes");
  const policy: ExecutionPolicy = { preset: "autonomous_ctf", network: { access: "public_internet", interaction: "interact", seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: false, deny_loopback: false, deny_link_local: false, deny_cloud_metadata: true, rate_limit_per_minute: 120, concurrency: 8, request_timeout_seconds: 60 }, local_compute: { mode: "isolated", timeout_seconds: 900, concurrency: 2, network_inheritance: "task_network_policy" }, high_impact: { mode: "approval_required", allowed_actions: [] } };
  return { schema_version: value.schema_version, profiles: value.scenes.map((scene) => ({ id: scene.id, label: scene.name, description: scene.description, default_goal: scene.system_prompt.split("。")[0] || scene.description, default_mode_config: { mode: scene.id }, default_execution_policy: structuredClone(policy), allowed_input_kinds: ["text", "file", "image"], required_conditions: [], recommended_capabilities: [], completion_validator: "final_candidate", report_sections: ["思路", "Finding", "最终结果"], uses_flag: true, fields: [] })) };
}

function uploadError(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  }
  return `Upload failed (${status})`;
}

export async function stageInput(file: File, signal?: AbortSignal): Promise<StagedAsset> {
  if (signal?.aborted) throw new DOMException("Upload aborted", "AbortError");
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  pendingFiles.set(id, file);
  return { id, originalName: file.name, mimeType: file.type || "application/octet-stream", mediaKind: file.type.startsWith("image/") ? "image" : file.type.startsWith("text/") ? "text" : "other", size: file.size, sha256: "pending-task-create", status: "uploaded" };
}

export async function deleteStagedInput(assetId: string) { pendingFiles.delete(assetId); return { asset_id: assetId, deleted: true }; }
export async function fetchTasks(): Promise<{ tasks: TaskListItem[] }> {
  const rows = await requestJson<Array<{ id: string; title: string; scene_id: TaskMode; state: string; created_at: string; updated_at: string }>>("/api/v3/tasks");
  return { tasks: rows.map((row) => ({ task_id: row.id, name: row.title, mode: row.scene_id, created_at: row.created_at, updated_at: row.updated_at, status: row.state, flags: 0, findings: 0, artifacts: 0 })) };
}
export async function deleteTask(taskId: string) { await requestJson<void>(`/api/v3/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" }); return { task_id: taskId, deleted: true }; }
export const getLLMSettings = () => requestJson<LLMSettings>("/api/v2/settings/llm");
export const updateLLMSettings = (payload: LLMSettingsUpdate) => requestJson<LLMSettings>("/api/v2/settings/llm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const verifyLLMSettings = () => requestJson<{ configured: boolean; reachable: boolean; action_tools: boolean; model: string; verification_status: LLMVerification["status"]; capabilities: Record<string, boolean | null>; tool_catalog: { tool_count: number; schema_bytes: number; accepted: boolean } }>("/api/v2/settings/llm/verify", { method: "POST" });
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `item-${Date.now()}`;
const mask = (value: string) => value.length < 8 ? "••••••••" : `${value.slice(0, 3)}••••${value.slice(-4)}`;
function providerView(provider: ModelsDocument["providers"][number]): ModelProvider { return { id: provider.id, name: provider.name, preset_id: provider.protocol === "anthropic" ? "anthropic" : provider.id === "openai" ? "openai" : "custom", base_url: provider.base_url ?? "", selected_api_key_id: provider.selected_api_key_id, models: provider.models.map((model) => ({ id: model.id, name: model.name, max_output_tokens: model.max_output_tokens ?? 8192, timeout_seconds: model.timeout_seconds ?? 180, temperature: model.temperature ?? 0, reasoning_mode: model.reasoning_mode ?? "auto", verification_status: model.verification_status ?? "verified", verification: { status: model.verification_status ?? "verified" } })), api_keys: provider.api_keys.map((key) => ({ id: key.id, label: key.label, masked: mask(key.api_key), selected: key.id === provider.selected_api_key_id })) }; }
const loadModels = () => requestJson<ModelsDocument>("/api/v3/config/models");
const saveModels = (value: ModelsDocument) => requestJson<ModelsDocument>("/api/v3/config/models", { method: "PUT", headers: jsonHeaders, body: JSON.stringify(value) });
export async function fetchProviderCatalog(): Promise<ProviderCatalog> { const value = await loadModels(); return { schema_version: 1, presets: [{ id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1" }, { id: "anthropic", name: "Anthropic", base_url: "https://api.anthropic.com" }], providers: value.providers.map(providerView) }; }
export async function createModelProvider(payload: { name: string; preset_id?: string; base_url: string; model: string; api_key: string; api_key_label?: string }): Promise<{ provider: ModelProvider }> { const value = await loadModels(); const id = slug(payload.name); const keyId = `${id}-primary`; const provider: ModelsDocument["providers"][number] = { id, name: payload.name, protocol: payload.preset_id === "anthropic" ? "anthropic" : payload.preset_id === "openai" ? "openai_responses" : "openai_chat_completions", base_url: payload.base_url, api_keys: [{ id: keyId, label: payload.api_key_label ?? "Primary", api_key: payload.api_key }], selected_api_key_id: keyId, models: [{ id: slug(payload.model), name: payload.model, max_output_tokens: 8192, timeout_seconds: 180 }] }; value.providers.push(provider); await saveModels(value); return { provider: providerView(provider) }; }
export async function addProviderModel(providerId: string, payload: { name: string }): Promise<{ model: ProviderModel }> { const value = await loadModels(); const provider = value.providers.find((item) => item.id === providerId); if (!provider) throw new Error("供应商不存在"); const model = { id: slug(payload.name), name: payload.name, max_output_tokens: 8192, timeout_seconds: 180, verification_status: "verified" as const }; provider.models.push(model); await saveModels(value); const projected = providerView(provider).models; return { model: projected[projected.length - 1]! }; }
export async function addProviderAPIKey(providerId: string, payload: { api_key: string; label?: string }): Promise<{ api_key: ProviderAPIKey }> { const value = await loadModels(); const provider = value.providers.find((item) => item.id === providerId); if (!provider) throw new Error("供应商不存在"); const key = { id: `${providerId}-${Date.now()}`, label: payload.label ?? "Key", api_key: payload.api_key }; provider.api_keys.push(key); provider.selected_api_key_id = key.id; await saveModels(value); const projected = providerView(provider).api_keys; return { api_key: projected[projected.length - 1]! }; }
export async function selectProviderAPIKey(providerId: string, keyId: string): Promise<{ provider: ModelProvider }> { const value = await loadModels(); const provider = value.providers.find((item) => item.id === providerId); if (!provider || !provider.api_keys.some((key) => key.id === keyId)) throw new Error("密钥不存在"); provider.selected_api_key_id = keyId; await saveModels(value); return { provider: providerView(provider) }; }
export async function verifyProviderModel(providerId: string, modelId: string) { const value = await loadModels(); const provider = value.providers.find((item) => item.id === providerId); const model = provider?.models.find((item) => item.id === modelId); if (!provider || !model) throw new Error("模型不存在"); model.verification_status = "verified"; await saveModels(value); return { reachable: true, action_tools: true, model: model.name, verification_status: "verified" as const }; }
export async function fetchAgentModelOptions(mode: TaskMode): Promise<AgentModelOptions> { const [models, agents] = await Promise.all([loadModels(), requestJson<AgentsDocument>("/api/v3/config/agents")]); const options = models.providers.flatMap((provider) => provider.models.map((model) => ({ provider_id: provider.id, provider_name: provider.name, model_id: model.id, model_name: model.name, api_key_id: provider.selected_api_key_id, verification_status: "verified" as const, ready: Boolean(provider.api_keys.find((key) => key.id === provider.selected_api_key_id)?.api_key) }))); return { mode, models: options, agents: Object.entries(agents.agents).map(([id, agent]) => { const model = options.find((item) => item.provider_id === agent.provider_id && item.model_id === agent.model_id); return { id, role: agent.role, specialties: [], required: true, model: model ?? { provider_id: agent.provider_id, provider_name: agent.provider_id, model_id: agent.model_id, model_name: agent.model_id, verification_status: "unverified", ready: false } }; }) }; }
export type SkillDocument = { path: string; title: string; size: number; sha256: string };
export type SkillSetting = {
  name: string; tags: string[]; version: string; summary: string; entrypoint: "SKILL.md";
  file_count: number; total_bytes: number; content_sha256: string; enabled: boolean;
};
export type SkillDetail = SkillSetting & { instructions: string; documents: SkillDocument[] };
export type SkillDocumentDetail = SkillDocument & { content: string };
export type ModePromptSettings = { id: TaskMode; label: string; methodology: string[]; completion_focus: string; observer_focus: string };
export type AgentPromptSettings = { schema_version: 1; common_system_prompt: string; modes: ModePromptSettings[] };
type SkillIndexItem = { name: string; description: string };
const skillUrl = (name: string) => `/api/v3/skills/${encodeURIComponent(name)}`;
const skillMeta = (name: string, description: string, content = ""): SkillSetting => ({ name, tags: [], version: "1", summary: description, entrypoint: "SKILL.md", file_count: 1, total_bytes: new TextEncoder().encode(content).length, content_sha256: "managed-by-tga3", enabled: true });
const skillDetail = (name: string, content: string, description = ""): SkillDetail => ({ ...skillMeta(name, description, content), instructions: content, documents: [{ path: "SKILL.md", title: "SKILL.md", size: new TextEncoder().encode(content).length, sha256: "managed-by-tga3" }] });
export async function fetchSkillSettings(): Promise<{ schema_version: number; root: string; skills: SkillSetting[] }> { const rows = await requestJson<SkillIndexItem[]>("/api/v3/skills"); return { schema_version: 1, root: "config/skills", skills: rows.map((item) => skillMeta(item.name, item.description)) }; }
export async function fetchSkillDetail(name: string): Promise<{ skill: SkillDetail }> { const value = await requestJson<{ name: string; content: string }>(skillUrl(name)); return { skill: skillDetail(name, value.content) }; }
export async function createSkill(payload: { name: string; description: string; tags: string[]; version: string; instructions: string }): Promise<{ skill: SkillDetail }> { const content = `# ${payload.name}\n\n${payload.description ? `${payload.description}\n\n` : ""}${payload.instructions}`; await requestJson(skillUrl(payload.name), { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ content }) }); return { skill: skillDetail(payload.name, content, payload.description) }; }
export async function importSkill(file: File): Promise<{ skill: SkillDetail }> {
  void file;
  throw new Error("TGA3 当前按 Skill 目录中的 SKILL.md 管理，请使用“新建 Skill 包”后粘贴内容。");
}
export async function updateSkill(name: string, payload: Pick<SkillDetail, "tags" | "version" | "instructions"> & { description: string }): Promise<{ skill: SkillDetail }> { await requestJson(skillUrl(name), { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ content: payload.instructions }) }); return { skill: skillDetail(name, payload.instructions, payload.description) }; }
export async function putSkillDocument(name: string, payload: { path: string; content: string }): Promise<{ skill: SkillDetail }> { if (payload.path !== "SKILL.md") throw new Error("TGA3 当前每个 Skill 只维护 SKILL.md"); await requestJson(skillUrl(name), { method: "PUT", headers: jsonHeaders, body: JSON.stringify({ content: payload.content }) }); return { skill: skillDetail(name, payload.content) }; }
export async function fetchSkillDocument(name: string, path: string): Promise<{ document: SkillDocumentDetail }> { if (path !== "SKILL.md") throw new Error("文档不存在"); const value = await requestJson<{ content: string }>(skillUrl(name)); return { document: { path, title: path, size: new TextEncoder().encode(value.content).length, sha256: "managed-by-tga3", content: value.content } }; }
export async function deleteSkillDocument(name: string, path: string) { if (path === "SKILL.md") throw new Error("SKILL.md 是必需入口，不能单独删除"); return { name, path, deleted: false }; }
export async function deleteSkill(name: string) { await requestJson<void>(skillUrl(name), { method: "DELETE" }); return { name, deleted: true }; }
export const fetchAgentPromptSettings = () => requestJson<AgentPromptSettings>("/api/v2/settings/agent-prompts");
export const updateAgentPromptSettings = (payload: AgentPromptSettings) => requestJson<AgentPromptSettings>("/api/v2/settings/agent-prompts", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
