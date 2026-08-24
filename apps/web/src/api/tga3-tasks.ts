import type { TaskMode } from "../modes";
import { requestJson } from "./client";
import type { AgentsConfig, ModelsConfig, ProviderProtocol, ScenesConfig } from "./tga3-config";

export type TGA3TaskListItem = {
  id: string;
  title: string;
  scene_id: TaskMode;
  state: string;
  blackboard_seq?: number;
  dialogue_seq?: number;
  created_at: string;
  updated_at: string;
};

export type StagedAsset = {
  id: string;
  originalName: string;
  mimeType: string;
  mediaKind: "image" | "text" | "other";
  size: number;
  status: "uploaded" | "failed";
  previewUrl?: string;
  error?: string;
};

export type AgentModelChoice = {
  provider_id: string;
  provider_name: string;
  protocol: ProviderProtocol;
  model_id: string;
  model_name: string;
  ready: boolean;
};

export type AgentModelOptions = {
  scene_id: TaskMode;
  agents: Array<{
    id: string;
    role: "supervisor" | "worker" | "reporter";
    runtime: "openai_agents" | "claude_agent";
    display_name: string;
    model: AgentModelChoice;
  }>;
  models: AgentModelChoice[];
};

const pendingFiles = new Map<string, File>();

export function takeStagedFile(assetId: string): File | undefined {
  const file = pendingFiles.get(assetId);
  pendingFiles.delete(assetId);
  return file;
}

export async function stageInput(file: File, signal?: AbortSignal): Promise<StagedAsset> {
  if (signal?.aborted) throw new DOMException("Upload aborted", "AbortError");
  const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  pendingFiles.set(id, file);
  return {
    id,
    originalName: file.name,
    mimeType: file.type || "application/octet-stream",
    mediaKind: file.type.startsWith("image/") ? "image" : file.type.startsWith("text/") ? "text" : "other",
    size: file.size,
    status: "uploaded",
  };
}

export function deleteStagedInput(assetId: string): void {
  pendingFiles.delete(assetId);
}

export async function listTasks(): Promise<TGA3TaskListItem[]> {
  return requestJson<TGA3TaskListItem[]>("/api/v3/tasks");
}

export async function listScenes(): Promise<ScenesConfig> {
  return requestJson<ScenesConfig>("/api/v3/config/scenes");
}

export async function createTask(input: {
  title: string;
  sceneId: TaskMode;
  prompt: string;
  fileIds: string[];
  agentModels: Record<string, { provider_id: string; model_id: string; protocol: ProviderProtocol }>;
}): Promise<TGA3TaskListItem> {
  const form = new FormData();
  form.set("title", input.title);
  form.set("scene_id", input.sceneId);
  form.set("prompt", input.prompt);
  form.set("agent_models", JSON.stringify(input.agentModels));
  for (const id of input.fileIds) {
    const file = pendingFiles.get(id);
    if (file) form.append("files", file, file.name);
  }
  const task = await requestJson<TGA3TaskListItem>("/api/v3/tasks", { method: "POST", body: form });
  input.fileIds.forEach((id) => pendingFiles.delete(id));
  return task;
}

export async function fetchAgentModelOptions(sceneId: TaskMode): Promise<AgentModelOptions> {
  const [models, agents] = await Promise.all([
    requestJson<ModelsConfig>("/api/v3/config/models"),
    requestJson<AgentsConfig>("/api/v3/config/agents"),
  ]);
  const choices = models.providers.flatMap((provider) =>
    provider.models.flatMap((model) => provider.protocols.map((protocol) => ({
      provider_id: provider.id,
      provider_name: provider.name,
      protocol,
      model_id: model.id,
      model_name: model.name,
      ready: Boolean(provider.api_keys.find((key) => key.id === provider.selected_api_key_id)?.api_key),
    }))),
  );
  return {
    scene_id: sceneId,
    models: choices,
    agents: Object.entries(agents.agents).map(([id, agent]) => ({
      id,
      role: agent.role,
      runtime: agent.runtime,
      display_name: agent.display_name,
      model: choices.find((choice) =>
        choice.provider_id === agent.provider_id &&
        choice.model_id === agent.model_id &&
        choice.protocol === agent.protocol,
      ) ?? {
        provider_id: agent.provider_id,
        provider_name: agent.provider_id,
        protocol: agent.protocol,
        model_id: agent.model_id,
        model_name: agent.model_id,
        ready: false,
      },
    })),
  };
}
