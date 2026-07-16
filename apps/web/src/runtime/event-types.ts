export type TaskMode = "ctf" | "web_audit" | "code_audit" | "binary_ctf";
export type SessionStatus = "created" | "running" | "paused" | "blocked" | "completed" | "failed" | "cancelled";
export type ActionStatus = "proposed" | "approved" | "running" | "succeeded" | "failed" | "blocked" | "cancelled";
export type HypothesisStatus = "pending" | "testing" | "verified" | "rejected" | "inconclusive" | "superseded";
export type MemoryKind = "fact" | "evidence" | "failure_boundary" | "hint" | "constraint" | "decision";

export type RuntimeTask = { id: string; name: string; mode: TaskMode; target: string; scope: string[] };
export type RuntimeSolver = { id: string; role: string; status: string; model_name?: string; started_at?: string | null; finished_at?: string | null };
export type Hypothesis = {
  id: string; statement: string; attack_class: string; entry_point: string; rationale: string; next_test: string;
  status: HypothesisStatus; confidence: number; attempt_count: number; evidence_artifact_ids: string[];
  last_result: string; owner_solver_id?: string | null; created_at?: string; updated_at?: string;
};
export type MemoryEntry = { id: string; kind: MemoryKind; content: string; artifact_ids: string[]; source: string; supersedes_id?: string | null; created_at?: string; updated_at?: string };
export type RuntimeArtifact = { id: string; task_id?: string; kind: string; path: string; tool?: string | null; target?: string | null; created_at?: string; excerpt?: string; status?: number; method?: string; truncated?: boolean };
export type RuntimeAction = { id: string; capability: string; target: string; status: ActionStatus; hypothesis_id?: string | null; rationale?: string; summary?: string; artifact_ids: string[]; error?: { code?: string; message?: string } | null; created_at?: string; updated_at?: string; arguments?: Record<string, string | number | boolean | null> };
export type ConfirmedFlag = { value: string; evidence_artifact_id: string; created_at?: string };
export type RuntimeFinding = { id: string; title: string; target: string; severity: string; status: "candidate" | "confirmed" | "rejected"; evidence_artifact_id?: string | null; evidence_excerpt?: string | null; remediation?: string | null };
export type EventPayload = {
  action_id?: string; capability?: string; target?: string; hypothesis_id?: string; statement?: string; attack_class?: string;
  status?: string; summary?: string; rationale?: string; reason?: string; reminder?: string; value?: string; kind?: string;
  evidence_artifact_id?: string; artifact_ids?: string[]; finding_id?: string; role?: string; model_name?: string;
  max_turns?: number; action?: string; error?: { code?: string; message?: string }; board?: { hypotheses: Hypothesis[]; memory: MemoryEntry[] };
};
export type AgentEventType = "SESSION_STARTED" | "SESSION_STOPPED" | "SESSION_CONTROLLED" | "SOLVER_STARTED" | "SOLVER_STOPPED" | "HYPOTHESIS_CREATED" | "HYPOTHESIS_UPDATED" | "HYPOTHESIS_STALLED" | "ACTION_PROPOSED" | "ACTION_STARTED" | "ACTION_FINISHED" | "OBSERVER_REVIEWED" | "OBSERVER_FAILED" | "GATE_REJECTED" | "FLAG_CONFIRMED" | "FINDING_CONFIRMED" | "USER_HINT" | string;
export type RuntimeEvent = { id: string; task_id: string; seq: number; type: AgentEventType; solver_id?: string | null; payload: EventPayload; created_at: string };
export type RuntimeSnapshot = {
  task: RuntimeTask; session: { status: SessionStatus; turn_count: number; max_turns: number; active_solver_id?: string | null; stop_reason?: string | null };
  solvers: RuntimeSolver[]; board: { hypotheses: Hypothesis[]; memory: MemoryEntry[] }; actions: RuntimeAction[];
  flags: ConfirmedFlag[]; findings: RuntimeFinding[]; artifacts: RuntimeArtifact[]; events: RuntimeEvent[]; latest_seq: number;
};
export type Capability = { name: string; availability: string; risk: string; modes: string[]; tools?: Array<{ tool_id: string; availability: string; detail?: string }> };
export type MCPTool = { tool_id: string; risk: string; methods: Array<{ name: string; description?: string; input_schema?: Record<string, unknown> }> };
export type MCPCatalog = { availability: string; reason?: string; tools: MCPTool[] };
export type MCPHealthRecord = { tool: string; status: string; detail: string };
export type MCPHealth = { configured: boolean; checked_at?: string; records: MCPHealthRecord[] };
export type CapabilityCatalog = { capabilities: Capability[]; tools: MCPCatalog };
