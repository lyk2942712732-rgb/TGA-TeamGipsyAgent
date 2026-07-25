import { z } from "zod";
import { TASK_MODES, normalizeTaskMode } from "../modes";

const sessionStatus = z.enum(["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"]);
const strategyStatus = z.enum(["pending", "testing", "succeeded", "failed", "blocked"]);

export const AgentEventSchema = z.object({
  schema_version: z.number().int().positive().optional().default(2),
  id: z.string().or(z.number()).transform(String),
  task_id: z.string().optional().default(""),
  solver_id: z.string().nullable().optional(),
  seq: z.number().int().positive(),
  type: z.string(),
  payload: z.record(z.string(), z.unknown()).default({}),
  created_at: z.string().optional().default(""),
});

const SessionFileSchema = z.object({ id: z.string(), original_name: z.string(), stored_name: z.string(), relative_path: z.string(), mime_type: z.string(), size: z.number().int().nonnegative(), sha256: z.string(), kind: z.literal("task_input"), media_kind: z.enum(["image", "text", "document", "archive", "binary", "other"]), container_path: z.string().optional(), purpose: z.string().optional() });
const MemorySchema = z.object({ id: z.string(), kind: z.enum(["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]).catch("fact"), content: z.string(), artifact_ids: z.array(z.string()).default([]), source: z.string().default("runtime"), supersedes_id: z.string().nullable().optional(), created_at: z.string().optional(), updated_at: z.string().optional() });
const StrategyStepSchema = z.object({ id: z.string(), title: z.string(), instructions: z.string().default(""), expected_request: z.string().default(""), success_marker: z.string().default(""), failure_conditions: z.array(z.string()).default([]), next_step_id: z.string().nullable().optional(), risk: z.enum(["passive", "active", "destructive"]).catch("passive"), status: strategyStatus.catch("pending"), action_ids: z.array(z.string()).default([]), evidence_artifact_ids: z.array(z.string()).default([]), last_result: z.string().default("") });
const StrategyCardSchema = z.object({ id: z.string(), task_id: z.string(), title: z.string(), summary: z.string().default(""), claims: z.array(z.string()).default([]), prerequisites: z.array(z.string()).default([]), target_version_checks: z.array(z.string()).default([]), status: strategyStatus.catch("pending"), active_step_id: z.string().nullable().optional(), sources: z.array(z.object({ hint_id: z.string().nullable().optional(), url: z.string().nullable().optional(), artifact_id: z.string().nullable().optional(), extraction_status: z.enum(["not_requested", "blocked_out_of_scope", "failed", "extracted"]).catch("not_requested"), source_refs: z.array(z.string()).default([]) })).default([]), steps: z.array(StrategyStepSchema).default([]), created_at: z.string().optional(), updated_at: z.string().optional() });
const SkillSnapshotSchema = z.object({ name: z.string(), version: z.string(), origin: z.enum(["builtin", "custom"]), modes: z.array(z.preprocess(normalizeTaskMode, z.enum(TASK_MODES))).default([]), capabilities: z.array(z.string()).default([]), tags: z.array(z.string()).default([]), body: z.string(), content_sha256: z.string(), score: z.number().int().nonnegative(), selection_reasons: z.array(z.string()).default([]) });
const SkillBundleSnapshotSchema = z.object({ schema_version: z.literal(1), selector: z.string(), query_summary: z.string().default(""), skills: z.array(SkillSnapshotSchema).default([]), total_chars: z.number().int().nonnegative().default(0) });

const RuntimeTaskSchema = z.object({
  id: z.string().default(""),
  name: z.string().default("未命名任务"),
  mode: z.preprocess(normalizeTaskMode, z.enum(TASK_MODES)).catch("ctf"),
  goal: z.string().optional(),
  schema_version: z.number().int().positive().optional(),
  task_entry_url: z.string().nullable().optional(),
  model_snapshot: z.object({
    provider: z.string(),
    model: z.string(),
    verification_id: z.string(),
    verified_at: z.string(),
  }).passthrough().nullable().optional().transform((value) => value ?? undefined),
  session_input: z.object({ prompt: z.string().default(""), files: z.array(SessionFileSchema).default([]) }).default({ prompt: "", files: [] }),
  mcp_capabilities: z.object({ catalog_version: z.string(), server_ids: z.array(z.string()).default([]), tools: z.array(z.object({ provider_name: z.string(), server_id: z.string(), method: z.string(), description: z.string().optional() })).default([]) }).optional(),
  skill_bundle_snapshot: SkillBundleSnapshotSchema.nullable().optional().transform((value) => value ?? undefined),
  mode_config: z.record(z.string(), z.unknown()).optional(),
  execution_policy: z.record(z.string(), z.unknown()).optional(),
}).transform(({ session_input, ...task }) => ({ ...task, prompt: session_input.prompt, files: session_input.files }));

export const RuntimeSnapshotSchema = z.object({
  schema_version: z.number().int().positive().optional().default(2),
  task: RuntimeTaskSchema,
  session: z.object({ status: sessionStatus, turn_count: z.number().int().nonnegative(), max_turns: z.number().int().positive(), active_solver_id: z.string().nullable().optional(), stop_reason: z.string().nullable().optional(), started_at: z.string().nullable().optional(), finished_at: z.string().nullable().optional() }),
  solvers: z.array(z.object({ id: z.string(), role: z.literal("main").catch("main"), status: z.string().default("waiting"), model_name: z.string().optional(), started_at: z.string().nullable().optional(), finished_at: z.string().nullable().optional() })).default([]),
  challenge: z.object({ status: z.enum(["unknown", "active", "solved", "blocked", "expired"]).catch("unknown"), completion_proof_artifact_id: z.string().nullable().optional(), status_reason: z.string().default("") }).nullable().optional().transform((value) => value ?? { status: "unknown" as const, status_reason: "" }),
  runtime: z.object({ memory: z.array(MemorySchema).default([]), strategy_cards: z.array(StrategyCardSchema).default([]) }).default({ memory: [], strategy_cards: [] }),
  actions: z.array(z.object({ id: z.string(), solver_id: z.string().nullish().transform((value) => value ?? undefined), capability: z.string(), target: z.string().nullish().transform((value) => value ?? ""), actual_target: z.string().nullable().optional(), input_id: z.string().nullable().optional(), target_ref: z.string().nullable().optional(), authorization: z.record(z.string(), z.unknown()).default({}), provenance: z.record(z.string(), z.unknown()).default({}), status: z.enum(["proposed", "pending_approval", "approved", "running", "succeeded", "failed", "blocked", "cancelled", "rejected"]).catch("proposed"), risk: z.enum(["passive", "active", "destructive"]).optional(), strategy_card_id: z.string().nullable().optional(), strategy_step_id: z.string().nullable().optional(), rationale: z.string().nullish().transform((value) => value ?? undefined), expected_outcome: z.string().default(""), retry_reason: z.string().default(""), alternative_analysis: z.string().default(""), effect: z.object({ scope: z.enum(["none", "session", "workspace", "target"]), persistence: z.enum(["none", "temporary", "persistent"]), reversibility: z.enum(["not_applicable", "reversible", "uncertain", "irreversible"]), category: z.enum(["authentication", "submission", "file_write", "resource_create", "resource_modify", "resource_delete", "containment", "destructive_scan"]), description: z.string() }).optional(), approval_expires_at: z.string().nullable().optional(), summary: z.string().nullish().transform((value) => value ?? ""), artifact_ids: z.array(z.string()).default([]), arguments: z.record(z.string(), z.unknown()).optional(), error: z.object({ code: z.string().optional(), message: z.string().optional() }).nullable().optional(), created_at: z.string().nullish().transform((value) => value ?? undefined), updated_at: z.string().nullish().transform((value) => value ?? undefined) })).default([]),
  flags: z.array(z.object({ value: z.string(), evidence_artifact_id: z.string(), created_at: z.string().optional() })).default([]),
  findings: z.array(z.object({ id: z.string(), title: z.string(), target: z.string(), severity: z.string(), status: z.enum(["candidate", "confirmed", "rejected"]), evidence_artifact_id: z.string().nullable().optional(), evidence_excerpt: z.string().nullable().optional(), remediation: z.string().nullable().optional() })).default([]),
  artifacts: z.array(z.object({ id: z.string(), task_id: z.string().optional(), kind: z.string(), path: z.string(), sha256: z.string().optional(), tool: z.string().nullable().optional(), target: z.string().nullable().optional(), input_id: z.string().nullable().optional(), provenance: z.record(z.string(), z.unknown()).default({}), created_at: z.string().optional(), excerpt: z.string().optional(), status: z.number().optional(), method: z.string().optional(), truncated: z.boolean().optional() })).default([]),
  artifact_indexes: z.array(z.object({ artifact_id: z.string(), document_type: z.string(), extraction_status: z.string(), summary: z.string().default(""), segment_count: z.number().int().nonnegative().default(0), source_refs: z.array(z.string()).default([]) })).default([]),
  http_sessions: z.array(z.object({ profile: z.string().default("persistent"), active: z.boolean().optional(), origin: z.string().optional(), origin_count: z.number().int().nonnegative().default(0), request_count: z.number().int().nonnegative().default(0), rebuild_count: z.number().int().nonnegative().default(0), reused: z.boolean().optional(), cross_process_recovery: z.boolean().default(false) })).default([]),
  observer: z.object({ directives: z.array(z.record(z.string(), z.unknown())).default([]) }).default({ directives: [] }),
  context_metrics: z.array(z.object({ turn: z.number().int().nonnegative(), audit_message_count: z.number().int().nonnegative(), working_message_count: z.number().int().nonnegative(), working_chars: z.number().int().nonnegative(), summary_hits: z.number().int().nonnegative().default(0), artifact_retrievals: z.number().int().nonnegative().default(0), provider_input_tokens: z.number().int().nonnegative().nullish().transform((value) => value ?? undefined), provider_output_tokens: z.number().int().nonnegative().nullish().transform((value) => value ?? undefined) })).default([]),
  events: z.array(AgentEventSchema).default([]),
  latest_seq: z.number().int().nonnegative(),
});
