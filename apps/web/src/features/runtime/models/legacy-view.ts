import { normalizeTaskMode } from "../../../modes";
import type { RuntimeSnapshot } from "../../../runtime/event-types";
import type { RuntimeStore } from "./types";

/**
 * Compatibility projection used only by the feature-flagged pre-Phase-10 page.
 * New components consume RuntimeStore and never inspect schema_version.
 */
export function projectLegacyRuntimeView(store: RuntimeStore): RuntimeSnapshot {
  const taskRaw = store.task.raw;
  return {
    schema_version: store.schemaVersion,
    task: {
      ...taskRaw,
      id: store.task.id,
      name: store.task.name,
      mode: normalizeTaskMode(store.task.mode),
      goal: store.task.goal,
      prompt: typeof taskRaw.prompt === "string" ? taskRaw.prompt : store.task.goal,
      files: Array.isArray(taskRaw.files) ? taskRaw.files as RuntimeSnapshot["task"]["files"] : [],
    },
    session: {
      status: store.session.status as RuntimeSnapshot["session"]["status"],
      turn_count: store.session.turnCount,
      max_turns: Math.max(1, store.session.maxTurns),
      active_solver_id: store.session.supervisorSolverId,
      stop_reason: store.session.stopReason,
      started_at: store.session.timestamps.started_at ?? null,
      finished_at: store.session.timestamps.finished_at ?? null,
    },
    solvers: Object.values(store.solversById).map((solver) => ({
      id: solver.solverId,
      role: "main" as const,
      status: solver.status,
      model_name: typeof solver.modelSnapshot.model === "string" ? solver.modelSnapshot.model : undefined,
      started_at: solver.timestamps.started_at ?? null,
      finished_at: solver.timestamps.finished_at ?? null,
    })),
    challenge: { status: "unknown", status_reason: "" },
    runtime: { memory: [], strategy_cards: [] },
    actions: Object.values(store.actionsById).map((action) => ({
      id: action.actionId, solver_id: action.solverId ?? undefined,
      capability: action.capability, target: action.target,
      status: action.status as RuntimeSnapshot["actions"][number]["status"],
      risk: action.risk as RuntimeSnapshot["actions"][number]["risk"],
      summary: action.summary, artifact_ids: action.artifactIds,
      arguments: action.arguments, created_at: action.createdAt, updated_at: action.updatedAt,
    })),
    flags: [],
    findings: Object.values(store.findingsById).map((finding) => ({
      id: finding.findingId, title: finding.title, target: finding.target ?? "",
      severity: finding.severity,
      status: finding.status as RuntimeSnapshot["findings"][number]["status"],
      evidence_excerpt: finding.descriptionPreview,
    })),
    artifacts: Object.values(store.artifactsById).map((artifact) => ({
      id: artifact.artifactId, task_id: store.task.id, kind: artifact.kind,
      path: artifact.artifactId, sha256: artifact.sha256,
      tool: artifact.tool, target: artifact.target, created_at: artifact.createdAt,
    })),
    artifact_indexes: [], http_sessions: [], observer: { directives: [] }, context_metrics: [],
    events: Object.values(store.eventsBySeq).sort((a, b) => a.seq - b.seq).map((event) => ({
      schema_version: event.schemaVersion, id: event.id, task_id: event.taskId,
      seq: event.seq, type: event.type, solver_id: event.solverId,
      intent_id: event.intentId, payload: event.payload, created_at: event.createdAt,
    })),
    latest_seq: store.latestSeq,
  };
}
