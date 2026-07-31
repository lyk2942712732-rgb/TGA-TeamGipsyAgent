import { z } from "zod";
import type {
  RuntimeAction,
  RuntimeApproval,
  RuntimeArtifact,
  RuntimeBudgetUsage,
  RuntimeEvent,
  RuntimeEvidenceClaim,
  RuntimeFinding,
  RuntimeIntent,
  RuntimeKnowledgeItem,
  RuntimeRetrievalSummary,
  RuntimeSolver,
  RuntimeStore,
  RuntimeWorkerResult,
} from "./types";

const SnapshotEnvelopeSchema = z.object({
  schema_version: z.literal(6),
  task: z.record(z.string(), z.unknown()),
  session: z.record(z.string(), z.unknown()).nullable(),
}).passthrough();

const EventEnvelopeSchema = z.object({
  schema_version: z.number().int().positive().default(6),
  id: z.union([z.string(), z.number()]).transform(String),
  task_id: z.string().default(""),
  seq: z.number().int().positive(),
  type: z.string(),
  solver_id: z.string().nullable().optional(),
  intent_id: z.string().nullable().optional(),
  payload: z.record(z.string(), z.unknown()).default({}),
  created_at: z.string().default(""),
}).passthrough();

export function normalizeRuntimeEvent(input: unknown): RuntimeEvent {
  const value = EventEnvelopeSchema.parse(input);
  return {
    schemaVersion: value.schema_version,
    id: value.id,
    taskId: value.task_id,
    seq: value.seq,
    type: value.type,
    solverId: value.solver_id ?? null,
    intentId: value.intent_id ?? null,
    payload: value.payload,
    createdAt: value.created_at,
  };
}

export function normalizeRuntimeSnapshot(input: unknown): RuntimeStore {
  const envelope = SnapshotEnvelopeSchema.parse(input);
  return normalizeV6(envelope);
}

function normalizeV6(snapshot: Record<string, unknown>): RuntimeStore {
  const task = record(snapshot.task);
  const session = record(snapshot.session);
  const team = record(snapshot.team);
  const events = array(snapshot.events).map(normalizeRuntimeEvent);
  return {
    schemaVersion: 6,
    task: {
      id: string(task.id), name: string(task.name, "未命名任务"), mode: string(task.mode, "ctf"),
      goal: string(task.goal ?? record(task.task_spec).objective), schemaVersion: 6, raw: task,
    },
    taskCommonSkillSnapshot: record(snapshot.task_common_skill_snapshot),
    session: {
      status: string(session.status, "created"),
      supervisorSolverId: nullableString(session.supervisor_solver_id),
      activeSolverCount: number(session.active_solver_count),
      maxActiveWorkers: Math.max(1, number(session.max_active_workers, 1)),
      taskBudgetUsage: budget(session.task_budget_usage),
      stopReason: nullableString(session.stop_reason),
      timestamps: timestamps(session.timestamps),
      turnCount: number(session.turn_count), maxTurns: number(session.max_turns),
    },
    team: {
      taskId: string(team.task_id, string(task.id)), status: string(team.status, string(session.status, "created")),
      supervisorSolverId: nullableString(team.supervisor_solver_id ?? session.supervisor_solver_id),
      maxActiveWorkers: Math.max(1, number(team.max_active_workers ?? session.max_active_workers, 1)),
      maxTotalSolvers: Math.max(1, number(team.max_total_solvers, 1)),
      activeSolverCount: number(team.active_solver_count ?? session.active_solver_count),
      solverIds: strings(team.solver_ids), version: Math.max(1, number(team.version, 1)),
      timestamps: timestamps(team.timestamps),
    },
    solversById: index(array(snapshot.solvers).map(solver), (item) => item.solverId),
    intentsById: index(array(snapshot.intents).map(intent), (item) => item.intentId),
    workerResultsById: index(array(snapshot.worker_results).map(workerResult), (item) => item.resultId),
    knowledgeById: index(array(snapshot.knowledge).map(knowledge), (item) => item.knowledgeId),
    artifactsById: index(array(snapshot.artifacts).map(artifact), (item) => item.artifactId),
    evidenceById: index(array(snapshot.evidence_claims).map(evidence), (item) => item.claimId),
    findingsById: index(array(snapshot.findings).map(finding), (item) => item.findingId),
    actionsById: index(array(snapshot.actions).map(action), (item) => item.actionId),
    approvalsById: index(array(snapshot.approvals).map(approval), (item) => item.approvalId),
    retrievalById: index(array(snapshot.retrieval_runs).map(retrieval), (item) => item.retrievalRunId),
    eventsBySeq: index(events, (item) => item.seq),
    globalPlan: snapshot.global_plan == null ? null : record(snapshot.global_plan),
    modeProjection: { challenge: record(snapshot.challenge), flags: array(snapshot.flags).map(record), artifactIndexes: array(snapshot.artifact_indexes).map(record) },
    latestSeq: number(snapshot.latest_seq, events[events.length - 1]?.seq ?? 0),
    eventHistoryHasMore: Boolean(record(snapshot.events_page).has_more),
    entitySequence: emptyEntitySequence(),
  };
}

function solver(value: unknown): RuntimeSolver { const item = record(value); return { taskId: string(item.task_id), solverId: string(item.solver_id), definitionId: string(item.definition_id), orchestrationRole: string(item.orchestration_role), specialties: strings(item.specialties), parentSolverId: nullableString(item.parent_solver_id), assignedIntentId: nullableString(item.assigned_intent_id), status: string(item.status), currentSummary: string(item.current_summary), modelSnapshot: record(item.model_snapshot), skillSnapshot: record(item.skill_snapshot), toolPolicySummary: record(item.tool_policy_summary ?? item.tool_policy), budgetUsage: budget(item.budget_usage), timestamps: timestamps(item.timestamps) }; }
function intent(value: unknown): RuntimeIntent { const item = record(value); return { taskId: string(item.task_id), intentId: string(item.intent_id), kind: string(item.kind), title: string(item.title), objective: string(item.objective), status: string(item.status), assignedSolverId: nullableString(item.assigned_solver_id), dependencies: strings(item.dependencies), priority: number(item.priority), budget: budget(item.budget), createdAt: string(item.created_at), updatedAt: string(item.updated_at) }; }
function workerResult(value: unknown): RuntimeWorkerResult { const item = record(value); return { resultId: string(item.result_id), solverId: string(item.solver_id), intentId: string(item.intent_id), status: string(item.status), summary: string(item.summary), artifactIds: strings(item.artifact_ids), evidenceClaimIds: strings(item.evidence_claim_ids), knowledgeIds: strings(item.knowledge_ids), findingIds: strings(item.finding_ids), limitations: strings(item.limitations), budgetUsage: budget(item.budget_usage) }; }
function knowledge(value: unknown): RuntimeKnowledgeItem { const item = record(value); return { knowledgeId: string(item.knowledge_id), scope: string(item.scope), targetId: nullableString(item.target_id), status: string(item.status), kind: string(item.kind), contentPreview: string(item.content_preview), contentSha256: string(item.content_sha256), createdBySolverId: nullableString(item.created_by_solver_id), createdAt: string(item.created_at) }; }
function artifact(value: unknown): RuntimeArtifact { const item = record(value); return { artifactId: string(item.artifact_id), intentId: nullableString(item.intent_id), kind: string(item.kind), mediaType: nullableString(item.media_type), tool: nullableString(item.tool), target: nullableString(item.target), sha256: string(item.sha256), createdAt: string(item.created_at) }; }
function evidence(value: unknown): RuntimeEvidenceClaim { const item = record(value); return { claimId: string(item.claim_id), statementPreview: string(item.statement_preview), artifactId: string(item.artifact_id), locator: record(item.locator), status: string(item.status), createdBySolverId: nullableString(item.created_by_solver_id), reviewedBySolverId: nullableString(item.reviewed_by_solver_id), createdAt: string(item.created_at), reviewedAt: nullableString(item.reviewed_at) }; }
function finding(value: unknown): RuntimeFinding { const item = record(value); return { findingId: string(item.finding_id), title: string(item.title), descriptionPreview: string(item.description_preview), target: nullableString(item.target), severity: string(item.severity), status: string(item.status), evidenceClaimIds: strings(item.evidence_claim_ids), createdBySolverId: nullableString(item.created_by_solver_id), createdAt: string(item.created_at), reviewedAt: nullableString(item.reviewed_at) }; }
function action(value: unknown): RuntimeAction { const item = record(value); return { actionId: string(item.action_id ?? item.id), solverId: nullableString(item.solver_id), intentId: nullableString(item.intent_id), capability: string(item.capability), target: string(item.target), risk: string(item.risk), effect: record(item.effect), arguments: record(item.arguments), status: string(item.status), summary: string(item.summary), artifactIds: strings(item.artifact_ids), createdAt: string(item.created_at), updatedAt: string(item.updated_at) }; }
function approval(value: unknown): RuntimeApproval { const item = record(value); return { approvalId: string(item.approval_id), solverId: string(item.solver_id), intentId: nullableString(item.intent_id), actionId: string(item.action_id), action: record(item.action), risk: string(item.risk), effect: record(item.effect), reason: string(item.reason), alternatives: strings(item.alternatives), deadline: string(item.deadline), status: string(item.status), createdAt: string(item.created_at), updatedAt: string(item.updated_at) }; }
function retrieval(value: unknown): RuntimeRetrievalSummary { const item = record(value); return { retrievalRunId: string(item.retrieval_run_id), ownerScope: string(item.owner_scope), workspaceId: nullableString(item.workspace_id), taskId: nullableString(item.task_id), solverId: nullableString(item.solver_id), intentId: nullableString(item.intent_id), indexSnapshotId: string(item.index_snapshot_id), method: string(item.method), queryPreview: string(item.query_preview), hitCount: number(item.hit_count), createdAt: string(item.created_at) }; }

function emptyEntitySequence(): RuntimeStore["entitySequence"] { return { solversById: {}, intentsById: {}, workerResultsById: {}, knowledgeById: {}, evidenceById: {}, findingsById: {}, approvalsById: {}, retrievalById: {} }; }
function index<T, K extends string | number>(items: T[], key: (value: T) => K): Record<K, T> { return Object.fromEntries(items.filter((item) => String(key(item))).map((item) => [key(item), item])) as Record<K, T>; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function array(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function string(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function nullableString(value: unknown): string | null { return typeof value === "string" && value ? value : null; }
function number(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function strings(value: unknown): string[] { return array(value).filter((item): item is string => typeof item === "string"); }
function budget(value: unknown): RuntimeBudgetUsage { return Object.fromEntries(Object.entries(record(value)).filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]))); }
function timestamps(value: unknown): Record<string, string | null> { return Object.fromEntries(Object.entries(record(value)).filter(([, item]) => item == null || typeof item === "string")) as Record<string, string | null>; }
