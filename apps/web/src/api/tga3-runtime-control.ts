import type { ProviderProtocol } from "./tga3-config";
import { apiBase, requestJson } from "./client";

export const tga3RuntimeApi = {
  reportUrl: (taskId: string) => `${apiBase}/api/v3/tasks/${encodeURIComponent(taskId)}/writeup/download`,
  stopTask: (taskId: string) => requestJson<void>(`/api/v3/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" }),
  setWorkerState: (taskId: string, agentId: string, action: "pause" | "resume") =>
    requestJson<{ actual_state: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/${action}`, { method: "POST" }),
  setWorkerModel: (taskId: string, agentId: string, providerId: string, modelId: string, protocol: ProviderProtocol) =>
    requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId, model_id: modelId, protocol }),
    }),
};
