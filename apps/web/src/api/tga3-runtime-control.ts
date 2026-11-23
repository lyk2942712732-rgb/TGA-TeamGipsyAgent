import type { ProviderProtocol } from "./tga3-config";
import { apiBase, requestJson } from "./client";

export type RestartTaskPayload = {
  rerun_scout?: boolean;
  retry_abandoned?: boolean;
  agent_models?: Record<string, { provider_id: string; model_id: string; protocol: ProviderProtocol }>;
  aoe_first_attempt_minutes?: number;
  aoe_second_attempt_minutes?: number;
  aoe_joint_minutes?: number;
};

export type RestartTaskResponse = {
  task: { id: string; state: string };
  resumed_from: "scout" | "workers" | "reporter";
  restart_count: number;
  fallback_models: string[];
  summary?: {
    resumed_from: string;
    need_scout: boolean;
    resumed_questions: number;
    requeued_abandoned: number;
    pending_reports: number;
  };
};

export const tga3RuntimeApi = {
  addPrompt: (taskId: string, text: string, key: string) => requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/blackboard/prompts`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, idempotency_key: key }),
  }),
  reportUrl: (taskId: string) => `${apiBase}/api/v3/tasks/${encodeURIComponent(taskId)}/writeup/download`,
  stopTask: (taskId: string) => requestJson<void>(`/api/v3/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" }),
  restartTask: (taskId: string, payload: RestartTaskPayload = {}) =>
    requestJson<RestartTaskResponse>(`/api/v3/tasks/${encodeURIComponent(taskId)}/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  extendQuestion: (taskId: string, questionId: string, minutes: number) =>
    requestJson<{ question_id: string; added_minutes: number; deadline: number; agents: string[] }>(
      `/api/v3/tasks/${encodeURIComponent(taskId)}/questions/${encodeURIComponent(questionId)}/extend`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ minutes }),
      },
    ),
  setWorkerState: (taskId: string, agentId: string, action: "pause" | "resume") =>
    requestJson<{ actual_state: string }>(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/${action}`, { method: "POST" }),
  setWorkerModel: (taskId: string, agentId: string, providerId: string, modelId: string, protocol: ProviderProtocol) =>
    requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId, model_id: modelId, protocol }),
    }),
};
