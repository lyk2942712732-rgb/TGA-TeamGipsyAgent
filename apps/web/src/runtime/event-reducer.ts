import type { ActionStatus, MemoryEntry, RuntimeAction, RuntimeEvent, RuntimeSnapshot, StrategyCard } from "./event-types";

const actionById = (actions: RuntimeAction[], id: string) => actions.find((item) => item.id === id);
const updateAction = (snapshot: RuntimeSnapshot, id: string, patch: Partial<RuntimeAction>) => ({
  ...snapshot,
  actions: snapshot.actions.map((action) => action.id === id ? { ...action, ...patch } : action),
});
const taskTarget = (snapshot: RuntimeSnapshot) => snapshot.task.task_entry_url ?? snapshot.task.id;

export function applyRuntimeEvent(snapshot: RuntimeSnapshot, event: RuntimeEvent): RuntimeSnapshot {
  if (event.seq <= snapshot.latest_seq || snapshot.events.some((item) => item.seq === event.seq)) return snapshot;
  const events = [...snapshot.events, event].sort((a, b) => a.seq - b.seq);
  let next: RuntimeSnapshot = { ...snapshot, events, latest_seq: Math.max(snapshot.latest_seq, event.seq) };
  const payload = event.payload;

  if (event.type === "SESSION_STARTED") next = { ...next, session: { ...next.session, status: "running", max_turns: numberValue(payload.max_turns, next.session.max_turns), active_solver_id: event.solver_id ?? next.session.active_solver_id } };
  if (event.type === "SESSION_STOPPED") next = { ...next, session: { ...next.session, status: sessionStatus(payload.status, next.session.status), stop_reason: stringValue(payload.reason) ?? null } };
  if (event.type === "SESSION_CONTROLLED" || event.type === "SESSION_STATUS_CHANGED") next = { ...next, session: { ...next.session, status: sessionStatus(payload.status, next.session.status) } };
  if (event.type === "APPROVAL_REQUESTED" || event.type === "ACTION_AWAITING_APPROVAL") next = { ...next, session: { ...next.session, status: "awaiting_approval" } };
  if (typeof payload.turn === "number") next = { ...next, session: { ...next.session, turn_count: Math.max(next.session.turn_count, payload.turn) } };

  if ((event.type === "ACTION_PROPOSED" || event.type === "TOOL_EXECUTION_START") && payload.action_id) {
    const status = event.type === "TOOL_EXECUTION_START" ? "running" : actionStatus(payload.status, "proposed");
    const patch: Partial<RuntimeAction> = { status, capability: stringValue(payload.capability) ?? stringValue(payload.tool_name) ?? "tool", strategy_card_id: stringValue(payload.strategy_card_id), strategy_step_id: stringValue(payload.strategy_step_id), rationale: stringValue(payload.rationale), expected_outcome: stringValue(payload.expected_outcome), arguments: recordValue(payload.arguments) };
    next = actionById(next.actions, payload.action_id) ? updateAction(next, payload.action_id, patch) : { ...next, actions: [...next.actions, { id: payload.action_id, capability: patch.capability ?? "tool", target: stringValue(payload.target) ?? taskTarget(next), status, artifact_ids: [], ...patch }] };
  }

  if ((event.type === "APPROVAL_REQUESTED" || event.type === "ACTION_APPROVAL_REQUIRED" || event.type === "ACTION_AWAITING_APPROVAL") && payload.action_id) {
    const patch: Partial<RuntimeAction> = {
      status: "pending_approval",
      capability: stringValue(payload.capability) ?? "tool",
      target: stringValue(payload.target) ?? taskTarget(next),
      risk: riskValue(payload.risk),
      rationale: stringValue(payload.rationale),
      expected_outcome: stringValue(payload.expected_outcome),
      alternative_analysis: stringValue(payload.alternative_analysis),
      effect: effectValue(payload.effect),
      approval_expires_at: stringValue(payload.approval_expires_at),
      arguments: recordValue(payload.arguments),
    };
    next = actionById(next.actions, payload.action_id) ? updateAction(next, payload.action_id, patch) : { ...next, actions: [...next.actions, { id: payload.action_id, capability: patch.capability ?? "tool", target: patch.target ?? taskTarget(next), status: "pending_approval", artifact_ids: [], ...patch }] };
  }

  if (event.type === "MANAGER_DECISION" && payload.action_id) {
    const status: ActionStatus = payload.decision === "denied" ? "rejected" : payload.decision === "approved" ? "approved" : actionStatus(payload.status, "proposed");
    const patch: Partial<RuntimeAction> = { status, authorization: recordValue(payload.authorization), strategy_card_id: stringValue(payload.strategy_card_id), strategy_step_id: stringValue(payload.strategy_step_id), expected_outcome: stringValue(payload.expected_outcome) };
    next = actionById(next.actions, payload.action_id) ? updateAction(next, payload.action_id, patch) : { ...next, actions: [...next.actions, { id: payload.action_id, capability: stringValue(payload.capability) ?? "tool", target: stringValue(payload.target) ?? taskTarget(next), artifact_ids: [], ...patch, status }] };
  }

  if (event.type === "ACTION_APPROVED" && payload.action_id) next = updateAction(next, payload.action_id, { status: "approved" });
  if ((event.type === "ACTION_REJECTED" || event.type === "ACTION_APPROVAL_EXPIRED" || event.type === "GATE_REJECTED") && payload.action_id) next = updateAction(next, payload.action_id, { status: "rejected", summary: stringValue(payload.reason) ?? stringValue(payload.summary) });
  if (event.type === "ACTION_CANCELLED" && payload.action_id) next = updateAction(next, payload.action_id, { status: "cancelled", summary: stringValue(payload.reason) ?? "任务已取消" });
  if (event.type === "TOOL_EXECUTION_END" && payload.action_id) next = updateAction(next, payload.action_id, { status: actionStatus(payload.status, "failed"), summary: stringValue(payload.summary), artifact_ids: stringArray(payload.artifact_ids).length ? stringArray(payload.artifact_ids) : payload.artifacts?.map((item) => item.artifact_id) ?? [], error: payload.error ?? null });

  if (event.type === "ARTIFACT_SAVED" && payload.artifact && !next.artifacts.some((item) => item.id === payload.artifact?.id)) next = { ...next, artifacts: [...next.artifacts, payload.artifact] };
  if (event.type === "FLAG_CONFIRMED" && typeof payload.value === "string" && typeof payload.evidence_artifact_id === "string" && !next.flags.some((flag) => flag.value === payload.value && flag.evidence_artifact_id === payload.evidence_artifact_id)) next = { ...next, flags: [...next.flags, { value: payload.value, evidence_artifact_id: payload.evidence_artifact_id, created_at: event.created_at }] };
  if (event.type === "CHALLENGE_STATUS_CHANGED") next = { ...next, challenge: { ...next.challenge, status: challengeStatus(payload.status, next.challenge.status), status_reason: stringValue(payload.reason) ?? next.challenge.status_reason, completion_proof_artifact_id: stringValue(payload.completion_proof_artifact_id) ?? next.challenge.completion_proof_artifact_id } };

  if (event.type === "USER_HINT" && payload.memory_id && typeof payload.content === "string") {
    next = { ...next, runtime: { ...next.runtime, memory: upsertMemory(next.runtime.memory, { id: payload.memory_id, kind: "hint", content: payload.content, artifact_ids: [], source: "user", created_at: event.created_at, updated_at: event.created_at }) } };
  }
  if (event.type === "MEMORY_UPSERTED") {
    if (payload.memory) next = { ...next, runtime: { ...next.runtime, memory: upsertMemory(next.runtime.memory, payload.memory) } };
    else if (payload.memory_id) next = { ...next, runtime: { ...next.runtime, memory: next.runtime.memory.map((memory) => memory.id === payload.memory_id ? { ...memory, kind: memoryKind(payload.kind, memory.kind), source: stringValue(payload.source) ?? memory.source, updated_at: event.created_at } : memory) } };
  }
  if (event.type === "STRATEGY_CARD_CREATED" && payload.strategy_card) next = { ...next, runtime: { ...next.runtime, strategy_cards: upsertStrategy(next.runtime.strategy_cards, payload.strategy_card) } };
  if (event.type === "STRATEGY_STEP_UPDATED" && payload.strategy_card_id && payload.strategy_step_id) next = { ...next, runtime: { ...next.runtime, strategy_cards: updateStrategy(next.runtime.strategy_cards, payload.strategy_card_id, payload.strategy_step_id, stringValue(payload.status), payload.action_id, stringArray(payload.artifact_ids), stringValue(payload.card_status), payload.active_step_id) } };
  return next;
}

export function mergeEvents(snapshot: RuntimeSnapshot, events: RuntimeEvent[]): RuntimeSnapshot {
  return [...events].sort((a, b) => a.seq - b.seq).reduce(applyRuntimeEvent, snapshot);
}

export function runtimeEventNeedsSnapshot(event: RuntimeEvent): boolean {
  if (["FINISH_ACCEPTED", "AGENT_FINISHED", "SESSION_STOPPED"].includes(event.type)) return true;
  if (event.type === "MEMORY_UPSERTED") return !event.payload.memory;
  if (event.type === "STRATEGY_CARD_CREATED") return !event.payload.strategy_card;
  return false;
}

function upsertMemory(memory: MemoryEntry[], value: MemoryEntry): MemoryEntry[] {
  return memory.some((item) => item.id === value.id) ? memory.map((item) => item.id === value.id ? { ...item, ...value } : item) : [...memory, value];
}
function upsertStrategy(cards: StrategyCard[], value: StrategyCard): StrategyCard[] {
  return cards.some((item) => item.id === value.id) ? cards.map((item) => item.id === value.id ? value : item) : [...cards, value];
}
function updateStrategy(cards: StrategyCard[], cardId: string, stepId: string, status?: string, actionId?: string, artifactIds: string[] = [], cardStatus?: string, activeStepId?: string | null): StrategyCard[] {
  return cards.map((card) => card.id !== cardId ? card : { ...card, status: strategyStatus(cardStatus, card.status), active_step_id: activeStepId === undefined ? card.active_step_id : activeStepId, steps: card.steps.map((step) => step.id !== stepId ? step : { ...step, status: strategyStatus(status, step.status), action_ids: actionId ? [...new Set([...step.action_ids, actionId])] : step.action_ids, evidence_artifact_ids: artifactIds.length ? [...new Set([...step.evidence_artifact_ids, ...artifactIds])] : step.evidence_artifact_ids }) });
}
function sessionStatus(value: unknown, fallback: RuntimeSnapshot["session"]["status"]): RuntimeSnapshot["session"]["status"] { return ["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"].includes(String(value)) ? value as RuntimeSnapshot["session"]["status"] : fallback; }
function actionStatus(value: unknown, fallback: ActionStatus): ActionStatus { return ["proposed", "pending_approval", "approved", "running", "succeeded", "failed", "blocked", "cancelled", "rejected"].includes(String(value)) ? value as ActionStatus : fallback; }
function strategyStatus(value: unknown, fallback: StrategyCard["status"]): StrategyCard["status"] { return ["pending", "testing", "succeeded", "failed", "blocked"].includes(String(value)) ? value as StrategyCard["status"] : fallback; }
function challengeStatus(value: unknown, fallback: RuntimeSnapshot["challenge"]["status"]): RuntimeSnapshot["challenge"]["status"] { return ["unknown", "active", "solved", "blocked", "expired"].includes(String(value)) ? value as RuntimeSnapshot["challenge"]["status"] : fallback; }
function memoryKind(value: unknown, fallback: MemoryEntry["kind"]): MemoryEntry["kind"] { return ["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"].includes(String(value)) ? value as MemoryEntry["kind"] : fallback; }
function recordValue(value: unknown): Record<string, unknown> | undefined { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
function stringValue(value: unknown): string | undefined { return typeof value === "string" ? value : undefined; }
function stringArray(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function numberValue(value: unknown, fallback: number): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function riskValue(value: unknown): RuntimeAction["risk"] { return ["passive", "active", "destructive"].includes(String(value)) ? value as RuntimeAction["risk"] : undefined; }
function effectValue(value: unknown): RuntimeAction["effect"] {
  const effect = recordValue(value);
  if (!effect) return undefined;
  const scope = String(effect.scope); const persistence = String(effect.persistence); const reversibility = String(effect.reversibility); const category = String(effect.category); const description = stringValue(effect.description);
  if (!["none", "session", "workspace", "target"].includes(scope) || !["none", "temporary", "persistent"].includes(persistence) || !["not_applicable", "reversible", "uncertain", "irreversible"].includes(reversibility) || !["authentication", "submission", "file_write", "resource_create", "resource_modify", "resource_delete", "containment", "destructive_scan"].includes(category) || !description) return undefined;
  return { scope, persistence, reversibility, category, description } as RuntimeAction["effect"];
}
