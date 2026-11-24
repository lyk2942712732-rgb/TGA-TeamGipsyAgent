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
  metadata?: { blackboard_cursor?: number; blackboard_read_at?: number; cli_session_id?: string; token_usage?: TGA3TokenUsage; scout_phase?: "collecting" | "watching" | "executing" | "extracting" | "merging" | "importing" | "completed"; intel_file_count?: number; intel_merged_count?: number; current_question_id?: string | null; current_question_title?: string | null; aoe_waiting?: boolean; attempt_deadline?: number | null };
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
export type AOEAttempt = { id: string; agent_id: string; phase: "solo" | "joint" | "swarm" | "reinforcement"; success_credit?: boolean; first_attempt_id?: string; previous_started_at?: number | null; previous_phase?: string; pauses?: Array<{ start: number; end: number | null }>; claimed_at?: number; started_at: number | null; deadline: number | null; manual_extension_seconds?: number; manual_extension_count?: number; superseded?: boolean; outcome: "solved" | "timeout" | "interrupted" | "error" | null; finished_at?: number; cli_session_id?: string | null };
export type AOEQuestion = {
  question_id: string; parent_task_id: string; title: string; description: string; source_key: string;
  difficulty: string | number | null; category?: string; tags?: string[];
  connection_info: string; submission_instructions: string; flag_format?: string; collection_notes?: string;
  pre_solved?: boolean; remote_attachments_count?: number;
  status: string; attempted_by: string[]; current_agents: string[]; attempts: AOEAttempt[]; flag: string | null;
  report_state: "pending" | "generating" | "completed" | "failed" | "disabled" | "pre_solved"; blackboard_seq: number;
  scout_ready?: boolean; scout_phase?: string;
  registered_at?: number; solved_at?: number | null;
  rescue?: { state: "seeking" | "reinforced" | "closed"; requested_at: number; threshold_at: number } | null;
  reinforcement?: { agent_id: string; joined_at: number; first_attempt_id: string } | null;
  finish_reason?: string | null;
  solver_agent_id?: string | null;
  swarm?: { entered_at: number; started_at: number | null; deadline: number | null } | null;
  attachments: Array<{ id: string; name: string; media_type: string; size_bytes: number; sha256: string }>;
};
export type AOEScoutWatch = {
  mode: "sentinel" | "llm_poll" | "manual";
  enabled: boolean;
  state: "collecting" | "watching" | "probing" | "idle" | "error_paused";
  probe_script_present: boolean;
  probe_interval_seconds: number;
  llm_interval_seconds: number | null;
  next_check_at: number | null;
  last_checked_at: number | null;
  last_check_kind: "sentinel" | "llm_scan" | null;
  last_result: string | null;
  last_new_count: number;
  last_solved_sync_count: number;
  last_error: string | null;
  consecutive_failures: number;
  scan_round: number;
  sentinel_checks_total: number;
};
export type AOEFinishConfirm = {
  status: "pending" | "continued" | "finishing";
  prompted_at?: number;
  decided_at?: number;
  wave_question_count?: number;
  solved_count?: number;
  abandoned_count?: number;
  pending_reports?: number;
  wait_for_reports?: boolean;
};
export type AOEOverview = {
  task_id: string;
  phase: string;
  attempt_seconds: number;
  attempt_budgets?: { first: number; second: number; joint: number };
  scout_mode?: string;
  collection_complete?: boolean;
  collection_status?: string;
  collection_warnings?: string[];
  scout_collecting?: boolean;
  scout_watch?: AOEScoutWatch | null;
  finish_confirm?: AOEFinishConfirm | null;
  auto_standby_on_complete?: boolean;
  scout_question_seconds?: number;
  questions: AOEQuestion[];
};
export type TGA3RuntimeSnapshot = { task: TGA3Task; agents: TGA3Agent[]; blackboard: TGA3BoardEntry[]; events: TGA3TaskEvent[]; aoe?: AOEOverview; experience_count?: number };
type TaskDetail = { task: TGA3Task; agents: TGA3Agent[]; experience_count?: number };

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
    return { task: detail.task, agents: detail.agents, blackboard: board.entries, events: activity.events, aoe, experience_count: detail.experience_count };
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
  return { task: detail.task, agents: detail.agents, blackboard: board.entries, events, experience_count: detail.experience_count };
}
