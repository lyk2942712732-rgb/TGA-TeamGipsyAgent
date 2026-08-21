import { requestJson } from "./client";

export type AttentionItem = {
  id: string;
  kind: "question" | "agent_paused" | "agent_failed" | "task_failed";
  task_id: string;
  task_title: string;
  task_state: string;
  agent_id: string | null;
  title: string;
  detail: string;
  question_id: string | null;
  created_at: string;
};

export const attentionApi = {
  list: () => requestJson<AttentionItem[]>("/api/v3/attention"),
  answer: (questionId: string, answer: string) => requestJson(`/api/v3/questions/${encodeURIComponent(questionId)}/answer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answer }) }),
  resume: (taskId: string, agentId: string) => requestJson(`/api/v3/tasks/${encodeURIComponent(taskId)}/agents/${encodeURIComponent(agentId)}/resume`, { method: "POST" }),
};
