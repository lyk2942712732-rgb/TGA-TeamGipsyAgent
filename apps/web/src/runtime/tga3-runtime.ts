import { requestJson } from "../api/client";

export type TGA3Task = {
  id: string; title: string; scene_id: string; state: string; blackboard_seq: number; event_seq: number;
  final_snapshot_seq?: number | null; created_at: string; updated_at: string;
  parent_task_id?: string; question_id?: string;
  metadata?: Record<string, unknown>;
};
export type TGA3TokenUsage = {
  input_tokens: number; output_tokens: number; cached_input_tokens: number;
  cache_write_input_tokens: number; total_tokens: number; reported: boolean; updated_at: string;
};
export type TGA3Agent = {
  agent_id: string; sdk: string; desired_state: string; actual_state: string; provider_id: string; model_id: string;
  model_name?: string; protocol: string; display_name: string; role: string; runtime_location: string; container_id?: string | null;
  session_id?: string | null; updated_at: string; last_error?: string | null;
  metadata?: { blackboard_cursor?: number; blackboard_read_at?: number; cli_session_id?: string; token_usage?: TGA3TokenUsage; scout_phase?: "collecting" | "executing" | "extracting" | "merging" | "importing" | "completed"; intel_file_count?: number; intel_merged_count?: number; current_question_id?: string | null; current_question_title?: string | null; aoe_waiting?: boolean; attempt_deadline?: number | null };
};
export type TGA3Actor = { agent_id: string; display_name: string; role: string; sdk?: string | null; model?: string | null };
export type TGA3BoardEntry = {
  id: string; seq: number; actor: TGA3Actor;
  kind: "user_prompt" | "user_file" | "intel" | "finding" | "final_candidate";
  topic?: string | null; body: Record<string, unknown>; idempotency_key?: string; created_at: string;
  question_id?: string;
};
export type TGA3TaskEvent = {
  id: string; seq: number; channel_agent_id: string; actor: TGA3Actor;
  kind: "assistant_delta" | "blackboard_progress" | "agent_status" | "action_started" | "action_completed" | "model_changed" | "paused" | "resumed" | "error" | "system";
  text: string; payload: Record<string, unknown>; created_at: string;
  question_id?: string;
};
export type AOEAttempt = { id: string; agent_id: string; phase: "solo" | "joint"; started_at: number; deadline: number; outcome: "solved" | "timeout" | "interrupted" | "error" | null; finished_at?: number; cli_session_id?: string | null };
export type AOEQuestion = {
  question_id: string; parent_task_id: string; title: string; description: string; source_key: string;
  difficulty: string | number | null; connection_info: string; submission_instructions: string;
  status: string; attempted_by: string[]; current_agents: string[]; attempts: AOEAttempt[]; flag: string | null;
  report_state: "pending" | "generating" | "completed" | "failed"; blackboard_seq: number;
  scout_ready?: boolean; scout_phase?: string;
  registered_at?: number; solved_at?: number | null;
  attachments: Array<{ id: string; name: string; media_type: string; size_bytes: number; sha256: string }>;
};
export type AOEOverview = { task_id: string; phase: string; attempt_seconds: number; scout_mode?: string; collection_complete?: boolean; scout_question_seconds?: number; questions: AOEQuestion[] };
export type TGA3RuntimeSnapshot = { task: TGA3Task; agents: TGA3Agent[]; blackboard: TGA3BoardEntry[]; events: TGA3TaskEvent[]; aoe?: AOEOverview };
type TaskDetail = { task: TGA3Task; agents: TGA3Agent[] };

/** Load the native TGA3 task, agent, blackboard, and activity event contracts. */
export async function loadTGA3Snapshot(taskId: string): Promise<TGA3RuntimeSnapshot> {
  const encoded = encodeURIComponent(taskId);
  const [detail, board, events] = await Promise.all([
    requestJson<TaskDetail>(`/api/v3/tasks/${encoded}`),
    requestJson<{ entries: TGA3BoardEntry[] }>(`/api/v3/tasks/${encoded}/blackboard?limit=500`),
    requestJson<TGA3TaskEvent[]>(`/api/v3/tasks/${encoded}/events?limit=500`),
  ]);
  if (detail.task.scene_id === "aoe" && !detail.task.parent_task_id) {
    const [aoe, activity] = await Promise.all([
      requestJson<AOEOverview>(`/api/v3/tasks/${encoded}/questions`),
      requestJson<{ events: TGA3TaskEvent[] }>(`/api/v3/tasks/${encoded}/aoe/events?limit=500`),
    ]);
    return { task: detail.task, agents: detail.agents, blackboard: board.entries, events: activity.events, aoe };
  }
  if (detail.task.parent_task_id) {
    // Child boards and events have independent sequence spaces. Load every page in order.
    let cursor = board.entries[board.entries.length - 1]?.seq ?? 0;
    while (cursor < detail.task.blackboard_seq) {
      const page = await requestJson<{ entries: TGA3BoardEntry[] }>(`/api/v3/tasks/${encoded}/blackboard?after_seq=${cursor}&limit=500`);
      if (!page.entries.length) break;
      board.entries.push(...page.entries); cursor = page.entries[page.entries.length - 1].seq;
    }
    let eventCursor = events[events.length - 1]?.seq ?? 0;
    while (eventCursor < detail.task.event_seq) {
      const page = await requestJson<TGA3TaskEvent[]>(`/api/v3/tasks/${encoded}/events?after_seq=${eventCursor}&limit=500`);
      if (!page.length) break;
      events.push(...page); eventCursor = page[page.length - 1].seq;
    }
  }
  return { task: detail.task, agents: detail.agents, blackboard: board.entries, events };
}
