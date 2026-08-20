export type TaskState =
  | "created"
  | "starting"
  | "running"
  | "waiting_user"
  | "finalizing"
  | "reporting"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentState =
  | "created"
  | "starting"
  | "running"
  | "pause_requested"
  | "paused"
  | "stopping"
  | "completed"
  | "failed"
  | "stopped";

export type TaskRun = {
  id: string;
  title: string;
  state: TaskState;
  blackboard_seq: number;
  dialogue_seq: number;
  final_snapshot_seq: number | null;
  created_at: string;
  updated_at: string;
};

export type AgentRun = {
  task_id: string;
  agent_id: "worker-openai" | "worker-claude" | string;
  sdk: "openai_agents" | "claude_agent";
  desired_state: AgentState;
  actual_state: AgentState;
  provider_id: string;
  model_id: string;
  container_id: string | null;
  session_id: string | null;
  last_error: string | null;
  updated_at: string;
};

export type Actor = {
  agent_id: string;
  display_name: string;
  role: "user" | "supervisor" | "worker" | "reporter" | "system";
  sdk: string | null;
  model: string | null;
};

export type EntryKind =
  | "user_prompt"
  | "user_file"
  | "supervisor_advice"
  | "finding"
  | "qa"
  | "final_candidate";

export type BlackboardEntry = {
  id: string;
  task_id: string;
  seq: number;
  actor: Actor;
  kind: EntryKind;
  topic: string;
  body: Record<string, unknown>;
  idempotency_key: string;
  created_at: string;
};

export type BlackboardSync = {
  published: BlackboardEntry | null;
  latest_seq: number;
  entries: BlackboardEntry[];
};

export type DialogueKind =
  | "assistant_delta"
  | "blackboard_progress"
  | "agent_status"
  | "action_started"
  | "action_completed"
  | "question"
  | "user_message"
  | "model_changed"
  | "paused"
  | "resumed"
  | "error"
  | "system";

export type DialogueMessage = {
  id: string;
  task_id: string;
  seq: number;
  channel_agent_id: string;
  actor: Actor;
  kind: DialogueKind;
  text: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type TaskDetail = { task: TaskRun; agents: AgentRun[] };

export type ModelRecord = {
  id: string;
  name: string;
  max_output_tokens: number;
  timeout_seconds: number;
  temperature?: number | null;
};

export type ProviderRecord = {
  id: string;
  name: string;
  protocol: "openai_responses" | "openai_chat_completions" | "anthropic";
  models: ModelRecord[];
};

export type ModelCatalog = {
  providers: ProviderRecord[];
  bindings: Record<string, {
    runtime: "openai_agents" | "claude_agent";
    provider_id: string;
    model_id: string;
    max_turns_per_cycle: number;
  }>;
};

export type SkillInfo = { name: string; description: string };
export type SkillDocument = { name: string; content: string };

export type UploadedFile = {
  file: {
    id: string;
    task_id: string;
    name: string;
    media_type: string;
    sha256: string;
    size_bytes: number;
  };
  blackboard_entry_id: string;
};

export type Writeup = {
  id: string;
  task_id: string;
  snapshot_seq: number;
  storage_path: string;
  sha256: string;
  created_at: string;
};
