import { z } from "zod";
import { TASK_MODES, normalizeTaskMode } from "../modes";

const status = z.enum(["created", "running", "paused", "blocked", "completed", "failed", "cancelled"]);
const role = z.enum(["main", "recon", "targeted", "research"]).catch("main");

// Events are append-only and payload fields evolve with the runtime. Preserve
// unknown payload keys so a new server event never prevents the console loading.
export const AgentEventSchema = z.object({
  schema_version: z.number().int().positive().optional().default(2),
  id: z.string().or(z.number()).transform(String), task_id: z.string().optional().default(""),
  solver_id: z.string().nullable().optional(), seq: z.number().int().positive(), type: z.string(),
  payload: z.record(z.string(), z.unknown()).default({}), created_at: z.string().optional().default(""),
});

const SolverSchema = z.object({
  id: z.string(), role, status: z.string().default("waiting"), model_name: z.string().optional(),
  parent_solver_id: z.string().nullable().optional(), started_at: z.string().nullable().optional(), finished_at: z.string().nullable().optional(),
});
const ChallengeSchema = z.object({
  status: z.enum(["unknown", "active", "solved", "blocked", "expired"]).catch("unknown"),
  completion_proof_artifact_id: z.string().nullable().optional(), status_reason: z.string().default(""),
});
const SubagentSchema = z.object({
  request: z.object({ id: z.string(), parent_solver_id: z.string(), role, objective: z.string().default(""), hypothesis_ids: z.array(z.string()).default([]), max_actions: z.number().int().positive().default(0) }),
  solver_id: z.string(), status: z.string().default("waiting"),
  output: z.object({ status: z.string().optional(), artifact_ids: z.array(z.string()).default([]), coverage_gaps: z.array(z.string()).default([]), next_recommendation: z.string().default("") }).nullable().default(null),
  created_at: z.string().optional(), updated_at: z.string().optional(),
});
const StrategyStepSchema = z.object({
  id: z.string(), title: z.string(), instructions: z.string().default(""), expected_request: z.string().default(""), success_marker: z.string().default(""),
  failure_conditions: z.array(z.string()).default([]), next_step_id: z.string().nullable().optional(), risk: z.enum(["passive", "active", "destructive"]).catch("passive"),
  status: z.enum(["pending", "testing", "verified", "rejected", "superseded"]).catch("pending"), action_ids: z.array(z.string()).default([]), evidence_artifact_ids: z.array(z.string()).default([]), last_result: z.string().default(""),
});
const StrategyCardSchema = z.object({
  id: z.string(), task_id: z.string(), title: z.string(), summary: z.string().default(""), claims: z.array(z.string()).default([]), prerequisites: z.array(z.string()).default([]),
  target_version_checks: z.array(z.string()).default([]), status: z.enum(["pending", "testing", "verified", "rejected", "superseded"]).catch("pending"), active_step_id: z.string().nullable().optional(),
  sources: z.array(z.object({ hint_id: z.string().nullable().optional(), url: z.string().nullable().optional(), artifact_id: z.string().nullable().optional(), extraction_status: z.enum(["not_requested", "blocked_out_of_scope", "failed", "extracted"]).catch("not_requested"), source_refs: z.array(z.string()).default([]) })).default([]),
  steps: z.array(StrategyStepSchema).default([]), created_at: z.string().optional(), updated_at: z.string().optional(),
});

const SessionFileSchema = z.object({
  id: z.string(), original_name: z.string(), stored_name: z.string(), relative_path: z.string(), mime_type: z.string(),
  size: z.number().int().nonnegative(), sha256: z.string(), kind: z.enum(["task", "hint"]),
  media_kind: z.enum(["image", "text", "document", "archive", "binary", "other"]),
});

export const RuntimeSnapshotSchema = z.object({
  schema_version: z.number().int().positive().optional().default(2),
  task: z.object({ id: z.string().default(""), name: z.string().default("未命名 Session"), mode: z.preprocess(normalizeTaskMode, z.enum(TASK_MODES)).catch("ctf"), target: z.string().default(""), scope: z.array(z.string()).default([]), goal: z.string().optional(), schema_version: z.number().int().positive().optional(), targets: z.array(z.record(z.string(), z.unknown())).default([]), hints: z.array(z.record(z.string(), z.unknown())).default([]), session_input: z.object({ task_files: z.array(SessionFileSchema).default([]), hint: z.object({ text: z.string().nullable().optional(), files: z.array(SessionFileSchema).default([]) }).default({ files: [] }) }).optional(), mcp_capabilities: z.object({ catalog_version: z.string(), server_ids: z.array(z.string()).default([]), tools: z.array(z.object({ provider_name: z.string(), server_id: z.string(), method: z.string(), description: z.string().optional() })).default([]) }).optional(), mode_config: z.record(z.string(), z.unknown()).optional(), execution_policy: z.record(z.string(), z.unknown()).optional() }),
  session: z.object({ status, turn_count: z.number().int().nonnegative(), max_turns: z.number().int().positive(), active_solver_id: z.string().nullable().optional(), stop_reason: z.string().nullable().optional() }),
  solvers: z.array(SolverSchema).default([]),
  challenge: ChallengeSchema.nullable().optional().transform((value) => value ?? { status: "unknown" as const, status_reason: "" }),
  subagents: z.array(SubagentSchema).default([]),
  board: z.object({
    hypotheses: z.array(z.object({ id: z.string(), statement: z.string(), attack_class: z.string(), entry_point: z.string(), rationale: z.string().default(""), next_test: z.string().default(""), status: z.enum(["pending", "testing", "verified", "rejected", "inconclusive", "superseded"]).catch("pending"), confidence: z.number().min(0).max(1).default(0), attempt_count: z.number().int().nonnegative().default(0), evidence_artifact_ids: z.array(z.string()).default([]), last_result: z.string().default(""), owner_solver_id: z.string().nullable().optional(), created_at: z.string().optional(), updated_at: z.string().optional() })).default([]),
    memory: z.array(z.object({ id: z.string(), kind: z.enum(["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]).catch("fact"), content: z.string(), artifact_ids: z.array(z.string()).default([]), source: z.string().default("runtime"), supersedes_id: z.string().nullable().optional(), created_at: z.string().optional(), updated_at: z.string().optional() })).default([]),
    strategy_cards: z.array(StrategyCardSchema).default([]),
  }),
  actions: z.array(z.object({ id: z.string(), solver_id: z.string().nullish().transform((value) => value ?? undefined), capability: z.string(), target: z.string().nullish().transform((value) => value ?? ""), actual_target: z.string().nullable().optional(), input_id: z.string().nullable().optional(), target_ref: z.string().nullable().optional(), authorization: z.record(z.string(), z.unknown()).default({}), provenance: z.record(z.string(), z.unknown()).default({}), status: z.enum(["proposed", "approved", "running", "succeeded", "failed", "blocked", "cancelled"]).catch("proposed"), hypothesis_id: z.string().nullable().optional(), strategy_card_id: z.string().nullable().optional(), strategy_step_id: z.string().nullable().optional(), rationale: z.string().nullish().transform((value) => value ?? undefined), expected_outcome: z.string().default(""), retry_reason: z.string().default(""), alternative_analysis: z.string().default(""), expected_side_effects: z.string().default(""), summary: z.string().nullish().transform((value) => value ?? ""), artifact_ids: z.array(z.string()).default([]), arguments: z.record(z.string(), z.unknown()).optional(), error: z.object({ code: z.string().optional(), message: z.string().optional() }).nullable().optional(), created_at: z.string().nullish().transform((value) => value ?? undefined), updated_at: z.string().nullish().transform((value) => value ?? undefined) })).default([]),
  flags: z.array(z.object({ value: z.string(), evidence_artifact_id: z.string(), created_at: z.string().optional() })).default([]),
  findings: z.array(z.object({ id: z.string(), title: z.string(), target: z.string(), severity: z.string(), status: z.enum(["candidate", "confirmed", "rejected"]), evidence_artifact_id: z.string().nullable().optional(), evidence_excerpt: z.string().nullable().optional(), remediation: z.string().nullable().optional() })).default([]),
  artifacts: z.array(z.object({ id: z.string(), task_id: z.string().optional(), kind: z.string(), path: z.string(), tool: z.string().nullable().optional(), target: z.string().nullable().optional(), input_id: z.string().nullable().optional(), provenance: z.record(z.string(), z.unknown()).default({}), created_at: z.string().optional(), excerpt: z.string().optional(), status: z.number().optional(), method: z.string().optional(), truncated: z.boolean().optional() })).default([]),
  artifact_indexes: z.array(z.object({ artifact_id: z.string(), document_type: z.string(), extraction_status: z.string(), summary: z.string().default(""), segment_count: z.number().int().nonnegative().default(0), source_refs: z.array(z.string()).default([]) })).default([]),
  http_sessions: z.array(z.object({ profile: z.string().default("persistent"), active: z.boolean().optional(), origin: z.string().optional(), origin_count: z.number().int().nonnegative().default(0), request_count: z.number().int().nonnegative().default(0), rebuild_count: z.number().int().nonnegative().default(0), reused: z.boolean().optional(), cross_process_recovery: z.boolean().default(false) })).default([]),
  observer: z.object({ directives: z.array(z.record(z.string(), z.unknown())).default([]) }).default({ directives: [] }),
  context_metrics: z.array(z.object({ turn: z.number().int().nonnegative(), audit_message_count: z.number().int().nonnegative(), working_message_count: z.number().int().nonnegative(), working_chars: z.number().int().nonnegative(), summary_hits: z.number().int().nonnegative().default(0), artifact_retrievals: z.number().int().nonnegative().default(0) })).default([]),
  events: z.array(AgentEventSchema).default([]), latest_seq: z.number().int().nonnegative(),
});
