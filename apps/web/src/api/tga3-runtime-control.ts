import type { ProviderProtocol } from "./tga3-config";
import { apiBase, requestJson } from "./client";
import type { AOEFinishConfirm, AOEScoutWatch } from "../runtime/tga3-runtime";

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
  wakeScout: (taskId: string) =>
    requestJson<{ started: boolean; reason: string; scout_watch: AOEScoutWatch }>(
      `/api/v3/tasks/${encodeURIComponent(taskId)}/scout/wake`,
      { method: "POST" },
    ),
  stopScout: (taskId: string) =>
    requestJson<{ stopped: boolean; mode: string; scout_watch: AOEScoutWatch }>(
      `/api/v3/tasks/${encodeURIComponent(taskId)}/scout/stop`,
      { method: "POST" },
    ),
  configureScoutWatch: (
    taskId: string,
    action: "resume_watch" | "enable_sentinel" | "enable_llm_poll" | "disable_llm_poll" | "disable",
    intervalSeconds?: number,
  ) =>
    requestJson<{ scout_watch: AOEScoutWatch }>(
      `/api/v3/tasks/${encodeURIComponent(taskId)}/scout/watch`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          ...(intervalSeconds !== undefined ? { interval_seconds: intervalSeconds } : {}),
        }),
      },
    ),
  submitFinishDecision: (
    taskId: string,
    decision: "continue_standby" | "finish_after_reports" | "finish_now",
    autoStandbyFuture = false,
  ) =>
    requestJson<{
      decision: string;
      phase: string;
      finish_confirm: AOEFinishConfirm;
      scout_watch: AOEScoutWatch;
    }>(
      `/api/v3/tasks/${encodeURIComponent(taskId)}/aoe/finish-decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, auto_standby_future: autoStandbyFuture }),
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
