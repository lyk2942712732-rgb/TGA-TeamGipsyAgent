import { requestJson } from "./client";

export type AttentionItem = {
  id: string;
  kind: "agent_paused" | "agent_failed" | "task_failed";
  task_id: string;
  task_title: string;
  task_state: string;
  agent_id: string | null;
  title: string;
  detail: string;
  created_at: string;
};

export const attentionApi = {
  list: () => requestJson<AttentionItem[]>("/api/v3/attention"),
  resume: (taskId: string, agentId: string) => requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/resume`, { method: "POST" }),
};
