import type { ProviderProtocol } from "./tga3-config";
import { apiBase, requestJson } from "./client";
import { takeStagedFile, type StagedAsset } from "./tga3-tasks";

async function uploadAttachments(taskId: string, attachments: StagedAsset[]): Promise<string[]> {
  const ids: string[] = [];
  for (const asset of attachments) {
    const file = takeStagedFile(asset.id);
    if (!file) continue;
    const form = new FormData();
    form.set("file", file, file.name);
    const uploaded = await requestJson<{ file: { id: string } }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/files`, { method: "POST", body: form });
    ids.push(uploaded.file.id);
  }
  return ids;
}

export const tga3RuntimeApi = {
  reportUrl: (taskId: string) => `${apiBase}/api/v3/tasks/${encodeURIComponent(taskId)}/writeup/download`,
  stopTask: (taskId: string) => requestJson<void>(`/api/v3/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" }),
  sendAgentPrompt: async (taskId: string, agentId: string, content: string, attachments: StagedAsset[]) => {
    const attachmentIds = await uploadAttachments(taskId, attachments);
    return requestJson<{ id: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/prompts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: content || "请查看附件",
        addressed_to: [agentId],
        attachment_ids: attachmentIds,
        idempotency_key: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`,
      }),
    });
  },
  answerQuestion: async (taskId: string, questionId: string, answer: string, attachments: StagedAsset[]) => {
    const attachmentIds = await uploadAttachments(taskId, attachments);
    return requestJson(`/api/v3/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer, attachment_ids: attachmentIds, idempotency_key: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}` }),
    });
  },
  setWorkerState: (taskId: string, agentId: string, action: "pause" | "resume") =>
    requestJson<{ actual_state: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/${action}`, { method: "POST" }),
  setWorkerModel: (taskId: string, agentId: string, providerId: string, modelId: string, protocol: ProviderProtocol) =>
    requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId, model_id: modelId, protocol }),
    }),
};
