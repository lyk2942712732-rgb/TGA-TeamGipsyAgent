import type { RuntimeAction, RuntimeEvent, RuntimeSnapshot } from "./event-types";

const actionById = (actions: RuntimeAction[], id: string) => actions.find((item) => item.id === id);
const updateAction = (snapshot: RuntimeSnapshot, id: string, patch: Partial<RuntimeAction>) => ({
  ...snapshot,
  actions: snapshot.actions.map((action) => action.id === id ? { ...action, ...patch } : action),
});

/** Applies only authoritative event fields; a debounced snapshot reconciliation fills richer board data. */
export function applyRuntimeEvent(snapshot: RuntimeSnapshot, event: RuntimeEvent): RuntimeSnapshot {
  if (event.seq <= snapshot.latest_seq || snapshot.events.some((item) => item.seq === event.seq)) return snapshot;
  const events = [...snapshot.events, event].sort((a, b) => a.seq - b.seq);
  let next: RuntimeSnapshot = { ...snapshot, events, latest_seq: event.seq };
  const payload = event.payload;
  if (event.type === "SESSION_STARTED") next = { ...next, session: { ...next.session, status: "running", max_turns: payload.max_turns ?? next.session.max_turns, active_solver_id: event.solver_id ?? next.session.active_solver_id } };
  if (event.type === "SESSION_STOPPED") next = { ...next, session: { ...next.session, status: (payload.status as RuntimeSnapshot["session"]["status"]) ?? next.session.status, stop_reason: payload.reason ?? null } };
  if (event.type === "SESSION_CONTROLLED" && payload.status) next = { ...next, session: { ...next.session, status: payload.status as RuntimeSnapshot["session"]["status"] } };
  if (event.type === "ACTION_PROPOSED" && payload.action_id && !actionById(next.actions, payload.action_id)) {
    next = { ...next, actions: [...next.actions, { id: payload.action_id, capability: payload.capability ?? "unknown", target: payload.target ?? "", hypothesis_id: payload.hypothesis_id ?? null, status: "proposed", rationale: payload.rationale, artifact_ids: [] }] };
  }
  if (event.type === "ACTION_STARTED" && payload.action_id) next = updateAction(next, payload.action_id, { status: "running" });
  if (event.type === "ACTION_FINISHED" && payload.action_id) next = updateAction(next, payload.action_id, { status: (payload.status as RuntimeAction["status"]) ?? "failed", summary: payload.summary, artifact_ids: payload.artifact_ids ?? [] });
  return next;
}

export function mergeEvents(snapshot: RuntimeSnapshot, events: RuntimeEvent[]): RuntimeSnapshot {
  return [...events].sort((a, b) => a.seq - b.seq).reduce(applyRuntimeEvent, snapshot);
}

/** Returns the exact persisted board state available at a replay cursor.

 * Older sessions never recorded board snapshots.  They deliberately return an
 * empty, unavailable board rather than claiming the current board existed at
 * an earlier event sequence.
 */
export function boardAtSeq(snapshot: RuntimeSnapshot, seq: number | null) {
  if (seq === null) return { board: snapshot.board, available: true };
  const events = [...snapshot.events]
    .sort((a, b) => a.seq - b.seq)
    .filter((item) => item.seq <= seq && item.type === "BOARD_SNAPSHOT");
  const event = events[events.length - 1];
  const board = event?.payload.board;
  if (!board || typeof board !== "object" || !Array.isArray((board as { hypotheses?: unknown }).hypotheses) || !Array.isArray((board as { memory?: unknown }).memory)) {
    return { board: { hypotheses: [], memory: [] }, available: false };
  }
  return { board: board as RuntimeSnapshot["board"], available: true };
}
