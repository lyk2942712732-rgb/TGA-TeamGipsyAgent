import { mergeRuntimeEvents } from "./reducer";
import { orderedEvents } from "./selectors";
import type { RuntimeStore } from "./types";

/** Rebuild the bounded UI projection from the persisted event window. */
export function replayStoreAtSeq(source: RuntimeStore, targetSeq: number): RuntimeStore {
  const events = orderedEvents(source);
  const firstSeq = events[0]?.seq ?? 1;
  const cursor = Math.max(firstSeq - 1, Math.min(Math.floor(targetSeq), source.latestSeq));
  const baseline: RuntimeStore = {
    ...source,
    session: { ...source.session, status: "created", activeSolverCount: 0, stopReason: null },
    team: { ...source.team, status: "created", activeSolverCount: 0 },
    solversById: Object.fromEntries(Object.entries(source.solversById).map(([id, solver]) => [id, { ...solver, status: "created", assignedIntentId: null, currentSummary: "" }])),
    intentsById: Object.fromEntries(Object.entries(source.intentsById).map(([id, intent]) => [id, { ...intent, status: "pending", assignedSolverId: null }])),
    workerResultsById: {}, knowledgeById: {}, evidenceById: {}, findingsById: {},
    actionsById: {}, approvalsById: {}, retrievalById: {}, globalPlan: null,
    eventsBySeq: {}, latestSeq: firstSeq - 1,
    entitySequence: { solversById: {}, intentsById: {}, workerResultsById: {}, knowledgeById: {}, evidenceById: {}, findingsById: {}, approvalsById: {}, retrievalById: {} },
  };
  if (cursor < firstSeq) return { ...baseline, latestSeq: cursor };
  const result = mergeRuntimeEvents(baseline, events.filter((event) => event.seq <= cursor));
  return result.gap ? baseline : result.state;
}
