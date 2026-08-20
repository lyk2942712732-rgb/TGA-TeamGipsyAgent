import type {
  AgentRun,
  BlackboardEntry,
  BlackboardSync,
  DialogueMessage,
  ModelCatalog,
  SceneInfo,
  SkillDocument,
  SkillInfo,
  TaskDetail,
  TaskRun,
  UploadedFile,
} from "./types";

const configuredBase = (import.meta.env.VITE_TGA3_API_BASE as string | undefined)?.trim();
const API_PREFIX = "/api/v3";

export function apiOrigin(pageOrigin = typeof window === "undefined" ? "" : window.location.origin): string {
  return (configuredBase || pageOrigin).replace(/\/$/, "");
}

export function apiUrl(path: string): string {
  return `${apiOrigin()}${API_PREFIX}${path}`;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      message?: string;
      detail?: string | Array<{ loc?: Array<string | number>; msg?: string }>;
    } | null;
    const detail = Array.isArray(body?.detail)
      ? body.detail.map((item) => `${item.loc?.slice(1).join(".") ?? "请求"}: ${item.msg ?? "无效"}`).join("；")
      : body?.detail;
    throw new ApiError(response.status, body?.message || detail || `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export const tga3Api = {
  listTasks: () => request<TaskRun[]>("/tasks"),
  createTask: (title: string, prompt: string, sceneId: string, files: File[]) => {
    const form = new FormData();
    form.append("title", title);
    form.append("prompt", prompt);
    form.append("scene_id", sceneId);
    files.forEach((file) => form.append("files", file));
    return request<TaskRun>("/tasks", { method: "POST", body: form });
  },
  getTask: (taskId: string) => request<TaskDetail>(`/tasks/${encodeURIComponent(taskId)}`),
  getBlackboard: (taskId: string) => request<BlackboardSync>(`/tasks/${encodeURIComponent(taskId)}/blackboard`),
  getDialogue: (taskId: string) => request<DialogueMessage[]>(`/tasks/${encodeURIComponent(taskId)}/dialogue`),
  addPrompt: (
    taskId: string,
    text: string,
    addressedTo: string[],
    attachmentIds: string[],
  ) => request<BlackboardEntry>(
    `/tasks/${encodeURIComponent(taskId)}/prompts`,
    json("POST", {
      text,
      addressed_to: addressedTo,
      attachment_ids: attachmentIds,
      idempotency_key: crypto.randomUUID(),
    }),
  ),
  uploadFile: async (taskId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadedFile>(`/tasks/${encodeURIComponent(taskId)}/files`, { method: "POST", body: form });
  },
  answerQuestion: (questionId: string, answer: string, attachmentIds: string[]) => request<BlackboardEntry>(
    `/questions/${encodeURIComponent(questionId)}/answer`,
    json("POST", {
      answer,
      attachment_ids: attachmentIds,
      idempotency_key: crypto.randomUUID(),
    }),
  ),
  pauseAgent: (taskId: string, agentId: string) => request<AgentRun>(
    `/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/pause`,
    { method: "POST" },
  ),
  resumeAgent: (taskId: string, agentId: string) => request<AgentRun>(
    `/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/resume`,
    { method: "POST" },
  ),
  setAgentModel: (taskId: string, agentId: string, providerId: string, modelId: string) => request<AgentRun>(
    `/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/model`,
    json("POST", { provider_id: providerId, model_id: modelId }),
  ),
  stopTask: (taskId: string) => request<void>(`/tasks/${encodeURIComponent(taskId)}/stop`, { method: "POST" }),
  models: () => request<ModelCatalog>("/models"),
  scenes: () => request<SceneInfo[]>("/scenes"),
  skills: () => request<SkillInfo[]>("/skills"),
  skill: (name: string) => request<SkillDocument>(`/skills/${encodeURIComponent(name)}`),
  writeupDownloadUrl: (taskId: string) => apiUrl(`/tasks/${encodeURIComponent(taskId)}/writeup/download`),
  dialogueStreamUrl: (taskId: string) => apiUrl(`/tasks/${encodeURIComponent(taskId)}/dialogue/stream`),
};
