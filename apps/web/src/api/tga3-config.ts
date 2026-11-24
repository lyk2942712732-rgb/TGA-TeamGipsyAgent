import { requestJson } from "./client";

export type TGA3Model = {
  id: string;
  name: string;
  enabled: boolean;
  available: boolean;
  max_output_tokens: number;
  timeout_seconds: number;
  temperature?: number | null;
  reasoning_mode?: "auto" | "enabled" | "disabled";
  verification_status?: "unverified" | "verifying" | "verified" | "failed" | "stale";
};
export type ProviderProtocol = "openai_responses" | "openai_chat_completions" | "anthropic";
export type AgentRuntime = "codex_cli" | "claude_cli" | "opencode_cli" | "qwen_cli";
export type ProviderEndpoint = { base_url: string; models: TGA3Model[] };
export type ProviderEndpoints = Partial<Record<ProviderProtocol, ProviderEndpoint>>;
export type TGA3Provider = {
  id: string;
  name: string;
  preset_id?: string;
  built_in?: boolean;
  endpoints: ProviderEndpoints;
  api_keys: Array<{ id: string; label: string; api_key: string }>;
  selected_api_key_id: string;
};
export type ModelsConfig = { schema_version: number; providers: TGA3Provider[] };
export type AgentConfig = { display_name: string; role: "scout" | "worker" | "reporter"; runtime: AgentRuntime; provider_id: string; model_id: string; protocol: ProviderProtocol; blackboard_interval_seconds?: number; system_prompt: string };
export type AgentsConfig = { schema_version: number; agents: Record<string, AgentConfig> };
export type SceneConfig = { id: string; name: string; description: string; system_prompt: string };
export type ScenesConfig = { schema_version: number; scenes: SceneConfig[] };
export type ImageHealthStatus = null | { status: "healthy" | "missing" | "unavailable"; available: boolean; detail: string; image_id?: string; size_bytes?: number; created?: string };
export type AgentDefinition = AgentConfig & {
  id: string;
  scout_timeout_seconds?: number;
  scout_extract_timeout_seconds?: number | null;
  tools: string[];
  image: string | null;
  image_health: ImageHealthStatus;
};
export type RuntimeDefinition = {
  id: AgentRuntime;
  display_name: string;
  supported_protocols: ProviderProtocol[];
  image: string | null;
  image_health: ImageHealthStatus;
  native_tools: string[];
  tools_by_role: Record<"scout" | "worker" | "reporter", string[]>;
};

const json = { "Content-Type": "application/json" };
export const tga3ConfigApi = {
  models: () => requestJson<ModelsConfig>("/api/v3/config/models"),
  agents: () => requestJson<AgentsConfig>("/api/v3/config/agents"),
  scenes: () => requestJson<ScenesConfig>("/api/v3/config/scenes"),
  agentDefinitions: () => requestJson<AgentDefinition[]>("/api/v3/agent-definitions"),
  runtimes: () => requestJson<RuntimeDefinition[]>("/api/v3/runtimes"),
  saveAgents: (value: AgentsConfig) => requestJson<AgentsConfig>("/api/v3/config/agents", { method: "PUT", headers: json, body: JSON.stringify(value) }),
  saveScoutSettings: (seconds: number) => requestJson<{ scout_timeout_seconds: number }>("/api/v3/config/scout-settings", { method: "PUT", headers: json, body: JSON.stringify({ timeout_seconds: seconds }) }),
  saveScenes: (value: ScenesConfig) => requestJson<ScenesConfig>("/api/v3/config/scenes", { method: "PUT", headers: json, body: JSON.stringify(value) }),
};
