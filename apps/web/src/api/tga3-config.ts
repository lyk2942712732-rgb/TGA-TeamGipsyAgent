import { requestJson } from "./client";

export type TGA3Model = { id: string; name: string; max_output_tokens: number; timeout_seconds: number; temperature?: number | null };
export type TGA3Provider = { id: string; name: string; protocol: "openai_responses" | "openai_chat_completions" | "anthropic"; base_url?: string | null; api_keys: Array<{ id: string; label: string; api_key: string }>; selected_api_key_id: string; models: TGA3Model[] };
export type ModelsConfig = { schema_version: number; providers: TGA3Provider[] };
export type AgentConfig = { display_name: string; role: "supervisor" | "worker" | "reporter"; runtime: "openai_agents" | "claude_agent"; provider_id: string; model_id: string; max_turns_per_cycle: number; system_prompt: string };
export type AgentsConfig = { schema_version: number; agents: Record<string, AgentConfig> };
export type SceneConfig = { id: string; name: string; description: string; system_prompt: string };
export type ScenesConfig = { schema_version: number; scenes: SceneConfig[] };
export type AgentDefinition = AgentConfig & {
  id: string;
  tools: string[];
  image: string | null;
  image_health: null | { status: "healthy" | "missing" | "unavailable"; available: boolean; detail: string; image_id?: string; size_bytes?: number; created?: string };
};

const json = { "Content-Type": "application/json" };
export const tga3ConfigApi = {
  models: () => requestJson<ModelsConfig>("/api/v3/config/models"),
  agents: () => requestJson<AgentsConfig>("/api/v3/config/agents"),
  scenes: () => requestJson<ScenesConfig>("/api/v3/config/scenes"),
  agentDefinitions: () => requestJson<AgentDefinition[]>("/api/v3/agent-definitions"),
  saveAgents: (value: AgentsConfig) => requestJson<AgentsConfig>("/api/v3/config/agents", { method: "PUT", headers: json, body: JSON.stringify(value) }),
  saveScenes: (value: ScenesConfig) => requestJson<ScenesConfig>("/api/v3/config/scenes", { method: "PUT", headers: json, body: JSON.stringify(value) }),
};
