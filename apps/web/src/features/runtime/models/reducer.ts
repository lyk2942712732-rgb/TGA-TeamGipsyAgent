import { reduceLegacyV5Event } from "./legacy-reducer";
import type {
  RuntimeApproval,
  RuntimeEvidenceClaim,
  RuntimeEvent,
  RuntimeIntent,
  RuntimeKnowledgeItem,
  RuntimeSolver,
  RuntimeStore,
  RuntimeRetrievalSummary,
  RuntimeWorkerResult,
} from "./types";

export type RuntimeReduction = { state: RuntimeStore; gap: boolean; needsRefresh: boolean };

const REFRESH_EVENTS = new Set([
  "PLAN_UPDATED", "KNOWLEDGE_PROMOTED", "KNOWLEDGE_CONFLICT_DETECTED",
  "EVIDENCE_CLAIM_CREATED", "EVIDENCE_CLAIM_REVIEWED", "WORKER_RESULT_MERGED",
  "RETRIEVAL_COMPLETED", "TASK_COMPLETION_PROPOSED",
]);

export function reduceRuntimeEvent(state: RuntimeStore, event: RuntimeEvent): RuntimeReduction {
  if (event.seq <= state.latestSeq || state.eventsBySeq[event.seq]) return { state, gap: false, needsRefresh: false };
  if (event.seq > state.latestSeq + 1) return { state, gap: true, needsRefresh: false };

  let next = state.legacy ? reduceLegacyV5Event(state, event) : reduceV6EntityEvent(state, event);
  const eventsBySeq = { ...next.eventsBySeq, [event.seq]: event };
  const sequences = Object.keys(eventsBySeq).map(Number).sort((a, b) => a - b);
  for (const seq of sequences.slice(0, Math.max(0, sequences.length - 500))) delete eventsBySeq[seq];
  next = { ...next, eventsBySeq, latestSeq: event.seq };
  return {
    state: next,
    gap: false,
    needsRefresh: REFRESH_EVENTS.has(event.type) && !hasEmbeddedProjection(event),
  };
}

export function mergeRuntimeEvents(state: RuntimeStore, events: RuntimeEvent[]): RuntimeReduction {
  let current = state;
  let needsRefresh = false;
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    const result = reduceRuntimeEvent(current, event);
    if (result.gap) return { state: current, gap: true, needsRefresh };
    current = result.state;
    needsRefresh ||= result.needsRefresh;
  }
  return { state: current, gap: false, needsRefresh };
}

function reduceV6EntityEvent(state: RuntimeStore, event: RuntimeEvent): RuntimeStore {
  const payload = event.payload;
  let next = state;
  const solverId = event.solverId ?? text(payload.solver_id);
  const intentId = event.intentId ?? text(payload.intent_id);

  if (event.type === "SOLVER_CREATED" && solverId && canUpdate(state, "solversById", solverId, event)) {
    const current = state.solversById[solverId];
    next = updateSolver(next, solverId, event, {
      ...(current ?? defaultSolver(state.task.id, solverId)),
      definitionId: text(payload.definition_id) ?? current?.definitionId ?? "runtime",
      orchestrationRole: text(payload.orchestration_role) ?? current?.orchestrationRole ?? "worker",
      parentSolverId: text(payload.parent_solver_id) ?? current?.parentSolverId ?? null,
      status: text(payload.status) ?? current?.status ?? "created",
    });
  }
  if (solverId && ["SOLVER_STARTED", "SOLVER_PAUSED", "SOLVER_COMPLETED", "SOLVER_FAILED"].includes(event.type) && canUpdate(next, "solversById", solverId, event)) {
    const status = ({ SOLVER_STARTED: "running", SOLVER_PAUSED: "paused", SOLVER_COMPLETED: "completed", SOLVER_FAILED: "failed" } as Record<string, string>)[event.type];
    next = updateSolver(next, solverId, event, { ...(next.solversById[solverId] ?? defaultSolver(state.task.id, solverId)), status, currentSummary: text(payload.summary) ?? next.solversById[solverId]?.currentSummary ?? "" });
  }

  if (event.type === "INTENT_CREATED" && intentId && canUpdate(next, "intentsById", intentId, event)) {
    next = updateIntent(next, intentId, event, { ...(next.intentsById[intentId] ?? defaultIntent(state.task.id, intentId)), title: text(payload.title) ?? intentId, objective: text(payload.objective) ?? "", kind: text(payload.kind) ?? "task", status: text(payload.status) ?? "pending" });
  }
  if (intentId && ["INTENT_ASSIGNED", "INTENT_CLAIMED", "INTENT_COMPLETED"].includes(event.type) && canUpdate(next, "intentsById", intentId, event)) {
    const current = next.intentsById[intentId] ?? defaultIntent(state.task.id, intentId);
    const status = event.type === "INTENT_ASSIGNED" ? "assigned" : event.type === "INTENT_CLAIMED" ? "running" : text(payload.status) ?? "completed";
    next = updateIntent(next, intentId, event, { ...current, status, assignedSolverId: solverId ?? current.assignedSolverId });
    if (solverId && next.solversById[solverId] && canUpdate(next, "solversById", solverId, event)) {
      next = updateSolver(next, solverId, event, { ...next.solversById[solverId], assignedIntentId: intentId, status: event.type === "INTENT_CLAIMED" ? "running" : next.solversById[solverId].status });
    }
  }

  if (event.type === "WORKER_RESULT_SUBMITTED") {
    const resultId = text(payload.worker_result_id);
    if (resultId && canUpdate(next, "workerResultsById", resultId, event)) {
      const result: RuntimeWorkerResult = { resultId, solverId: solverId ?? "", intentId: intentId ?? "", status: text(payload.status) ?? "submitted", summary: text(payload.summary) ?? "", artifactIds: texts(payload.artifact_ids), evidenceClaimIds: texts(payload.evidence_claim_ids), knowledgeIds: texts(payload.knowledge_ids), findingIds: texts(payload.finding_ids), limitations: texts(payload.limitations), budgetUsage: numericRecord(payload.budget_usage) };
      next = updateMap(next, "workerResultsById", resultId, result, event);
    }
  }
  if (event.type === "WORKER_RESULT_MERGED") {
    const resultId = text(payload.worker_result_id);
    if (resultId && canUpdate(next, "workerResultsById", resultId, event)) {
      const current = next.workerResultsById[resultId];
      const result: RuntimeWorkerResult = current
        ? { ...current, status: "merged", summary: text(payload.summary) ?? current.summary }
        : { resultId, solverId: solverId ?? "", intentId: intentId ?? "", status: "merged", summary: text(payload.summary) ?? "", artifactIds: [], evidenceClaimIds: [], knowledgeIds: [], findingIds: [], limitations: [], budgetUsage: {} };
      next = updateMap(next, "workerResultsById", resultId, result, event);
    }
  }

  if (["KNOWLEDGE_CANDIDATE_CREATED", "KNOWLEDGE_PROMOTED", "KNOWLEDGE_CONFLICT_DETECTED"].includes(event.type)) {
    const id = text(payload.knowledge_id) ?? text(payload.conflict_id);
    if (id && canUpdate(next, "knowledgeById", id, event)) {
      const current = next.knowledgeById[id];
      const item: RuntimeKnowledgeItem = { knowledgeId: id, scope: text(payload.scope) ?? current?.scope ?? "task", targetId: text(payload.target_id) ?? current?.targetId ?? null, status: event.type === "KNOWLEDGE_PROMOTED" ? "verified" : event.type === "KNOWLEDGE_CONFLICT_DETECTED" ? "conflict" : "candidate", kind: event.type === "KNOWLEDGE_CONFLICT_DETECTED" ? "conflict" : text(payload.kind) ?? current?.kind ?? "fact", contentPreview: text(payload.content_preview) ?? current?.contentPreview ?? "", contentSha256: text(payload.content_sha256) ?? current?.contentSha256 ?? "", createdBySolverId: solverId ?? current?.createdBySolverId ?? null, createdAt: text(payload.created_at) ?? current?.createdAt ?? event.createdAt };
      next = updateMap(next, "knowledgeById", id, item, event);
    }
  }

  if (["EVIDENCE_CLAIM_CREATED", "EVIDENCE_CLAIM_REVIEWED"].includes(event.type)) {
    const claimId = text(payload.evidence_claim_id);
    if (claimId && canUpdate(next, "evidenceById", claimId, event)) {
      const current = next.evidenceById[claimId];
      const item: RuntimeEvidenceClaim = {
        claimId,
        statementPreview: text(payload.statement_preview) ?? current?.statementPreview ?? "",
        artifactId: text(payload.artifact_id) ?? current?.artifactId ?? "",
        locator: object(payload.locator).kind ? object(payload.locator) : current?.locator ?? {},
        status: event.type === "EVIDENCE_CLAIM_REVIEWED" ? text(payload.status) ?? "confirmed" : current?.status ?? "candidate",
        createdBySolverId: current?.createdBySolverId ?? solverId,
        reviewedBySolverId: event.type === "EVIDENCE_CLAIM_REVIEWED" ? solverId : current?.reviewedBySolverId ?? null,
        createdAt: current?.createdAt ?? event.createdAt,
        reviewedAt: event.type === "EVIDENCE_CLAIM_REVIEWED" ? event.createdAt : current?.reviewedAt ?? null,
      };
      next = updateMap(next, "evidenceById", claimId, item, event);
    }
  }

  if (event.type === "RETRIEVAL_COMPLETED") {
    const runId = text(payload.retrieval_run_id);
    if (runId && canUpdate(next, "retrievalById", runId, event)) {
      const current = next.retrievalById[runId];
      const item: RuntimeRetrievalSummary = {
        retrievalRunId: runId, ownerScope: text(payload.owner_scope) ?? current?.ownerScope ?? "task",
        workspaceId: text(payload.workspace_id) ?? current?.workspaceId ?? null,
        taskId: text(payload.task_id) ?? current?.taskId ?? state.task.id,
        solverId: solverId ?? current?.solverId ?? null, intentId: intentId ?? current?.intentId ?? null,
        indexSnapshotId: text(payload.index_snapshot_id) ?? current?.indexSnapshotId ?? "",
        method: text(payload.method) ?? current?.method ?? "unknown",
        queryPreview: text(payload.query_preview) ?? current?.queryPreview ?? "",
        hitCount: typeof payload.hit_count === "number" ? payload.hit_count : current?.hitCount ?? 0,
        createdAt: current?.createdAt ?? event.createdAt,
      };
      next = updateMap(next, "retrievalById", runId, item, event);
    }
  }

  if (event.type === "APPROVAL_REQUESTED") {
    const approvalId = text(payload.approval_id);
    const actionId = text(payload.action_id);
    if (approvalId && actionId && canUpdate(next, "approvalsById", approvalId, event)) {
      const current = next.approvalsById[approvalId];
      const item: RuntimeApproval = { approvalId, solverId: solverId ?? current?.solverId ?? "", intentId: intentId ?? current?.intentId ?? null, actionId, action: object(payload.action), risk: text(payload.risk) ?? "active", effect: object(payload.effect), reason: text(payload.reason) ?? "", alternatives: texts(payload.alternatives), deadline: text(payload.deadline) ?? text(payload.approval_expires_at) ?? "", status: text(payload.status) ?? "pending", createdAt: current?.createdAt ?? event.createdAt, updatedAt: event.createdAt };
      next = updateMap(next, "approvalsById", approvalId, item, event);
      if (item.solverId && next.solversById[item.solverId] && canUpdate(next, "solversById", item.solverId, event)) next = updateSolver(next, item.solverId, event, { ...next.solversById[item.solverId], status: "awaiting_approval" });
    }
  }
  if (["ACTION_APPROVED", "ACTION_REJECTED", "ACTION_APPROVAL_EXPIRED"].includes(event.type)) {
    const actionId = text(payload.action_id);
    const status = event.type === "ACTION_APPROVED" ? "approved" : event.type === "ACTION_REJECTED" ? "rejected" : "expired";
    const approvalsById = Object.fromEntries(Object.entries(next.approvalsById).map(([id, approval]) => [id, approval.actionId === actionId ? { ...approval, status, updatedAt: event.createdAt } : approval]));
    next = { ...next, approvalsById };
    const resolved = Object.values(approvalsById).find((approval) => approval.actionId === actionId);
    if (resolved?.solverId && next.solversById[resolved.solverId] && !Object.values(approvalsById).some((approval) => approval.solverId === resolved.solverId && approval.status === "pending")) next = { ...next, solversById: { ...next.solversById, [resolved.solverId]: { ...next.solversById[resolved.solverId], status: "queued" } } };
  }

  if (event.type === "PLAN_UPDATED") {
    const embedded = object(payload.global_plan);
    next = { ...next, globalPlan: embedded.version !== undefined ? embedded : { ...(next.globalPlan ?? {}), version: typeof payload.new_version === "number" ? payload.new_version : next.globalPlan?.version } };
  }
  if (event.type === "TASK_COMPLETION_ACCEPTED") next = { ...next, session: { ...next.session, status: "completed" }, team: { ...next.team, status: "completed" } };
  if (event.type === "ORCHESTRATOR_STARTED") next = { ...next, session: { ...next.session, status: "running" }, team: { ...next.team, status: "running" } };

  const activeSolverCount = Object.values(next.solversById).filter((solver) => ["created", "queued", "ready", "running", "waiting", "awaiting_approval"].includes(solver.status)).length;
  return { ...next, session: { ...next.session, activeSolverCount }, team: { ...next.team, activeSolverCount, solverIds: Object.keys(next.solversById) } };
}

type SequencedMap = keyof RuntimeStore["entitySequence"];
function canUpdate(state: RuntimeStore, map: SequencedMap, id: string, event: RuntimeEvent): boolean { return entitySeq(event) > (state.entitySequence[map][id] ?? 0); }
function entitySeq(event: RuntimeEvent): number {
  if (typeof event.payload.entity_version === "number") return event.payload.entity_version;
  if (typeof event.payload.version === "number") return event.payload.version;
  if (typeof event.payload.entity_seq === "number") return event.payload.entity_seq;
  return event.seq;
}
function updateSolver(state: RuntimeStore, id: string, event: RuntimeEvent, value: RuntimeSolver): RuntimeStore { return updateMap(state, "solversById", id, value, event); }
function updateIntent(state: RuntimeStore, id: string, event: RuntimeEvent, value: RuntimeIntent): RuntimeStore { return updateMap(state, "intentsById", id, value, event); }
function updateMap<K extends SequencedMap>(state: RuntimeStore, map: K, id: string, value: RuntimeStore[K][string], event: RuntimeEvent): RuntimeStore {
  return { ...state, [map]: { ...state[map], [id]: value }, entitySequence: { ...state.entitySequence, [map]: { ...state.entitySequence[map], [id]: entitySeq(event) } } };
}
function defaultSolver(taskId: string, solverId: string): RuntimeSolver { return { taskId, solverId, definitionId: "runtime", orchestrationRole: "worker", specialties: [], parentSolverId: null, assignedIntentId: null, status: "created", currentSummary: "", modelSnapshot: {}, skillSnapshot: {}, toolPolicySummary: {}, budgetUsage: {}, timestamps: {} }; }
function defaultIntent(taskId: string, intentId: string): RuntimeIntent { return { taskId, intentId, kind: "task", title: intentId, objective: "", status: "pending", assignedSolverId: null, dependencies: [], priority: 0, budget: {}, createdAt: "", updatedAt: "" }; }
function hasEmbeddedProjection(event: RuntimeEvent): boolean { return ["global_plan", "knowledge", "conflict", "evidence_claim", "worker_result", "retrieval_run"].some((key) => event.payload[key] && typeof event.payload[key] === "object"); }
function text(value: unknown): string | null { return typeof value === "string" && value ? value : null; }
function texts(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function numericRecord(value: unknown): Record<string, number> { return Object.fromEntries(Object.entries(object(value)).filter((entry): entry is [string, number] => typeof entry[1] === "number")); }
