import { requestJson } from "../api/client";

export type TGA3Task = {
  id: string; title: string; scene_id: string; state: string; blackboard_seq: number; dialogue_seq: number;
  final_snapshot_seq?: number | null; created_at: string; updated_at: string;
};
export type TGA3Agent = {
  agent_id: string; sdk: string; desired_state: string; actual_state: string; provider_id: string; model_id: string;
  protocol: string; display_name: string; role: string; runtime_location: string; container_id?: string | null;
  session_id?: string | null; updated_at: string; last_error?: string | null;
};
export type TGA3Actor = { agent_id: string; display_name: string; role: string; sdk?: string | null; model?: string | null };
export type TGA3BoardEntry = {
  id: string; seq: number; actor: TGA3Actor;
  kind: "user_prompt" | "user_file" | "supervisor_advice" | "finding" | "qa" | "final_candidate";
  topic: string; body: Record<string, unknown>; idempotency_key?: string; created_at: string;
};
export type TGA3DialogueMessage = {
  id: string; seq: number; channel_agent_id: string; actor: TGA3Actor;
  kind: "assistant_delta" | "blackboard_progress" | "agent_status" | "action_started" | "action_completed" | "question" | "user_message" | "model_changed" | "paused" | "resumed" | "error" | "system";
  text: string; payload: Record<string, unknown>; created_at: string;
};
export type TGA3RuntimeSnapshot = { task: TGA3Task; agents: TGA3Agent[]; blackboard: TGA3BoardEntry[]; dialogue: TGA3DialogueMessage[] };
type TaskDetail = { task: TGA3Task; agents: TGA3Agent[] };

/** Load native TGA3 contracts. No legacy RuntimeStore projection is performed. */
export async function loadTGA3Snapshot(taskId: string): Promise<TGA3RuntimeSnapshot> {
  const encoded = encodeURIComponent(taskId);
  const [detail, board, dialogue] = await Promise.all([
    requestJson<TaskDetail>(`/api/v3/tasks/${encoded}`),
    requestJson<{ entries: TGA3BoardEntry[] }>(`/api/v3/tasks/${encoded}/blackboard?limit=500`),
    requestJson<TGA3DialogueMessage[]>(`/api/v3/tasks/${encoded}/dialogue?limit=500`),
  ]);
  return { task: detail.task, agents: detail.agents, blackboard: board.entries, dialogue };
}
