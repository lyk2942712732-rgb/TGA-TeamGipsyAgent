import type { RuntimeEvent, RuntimeStore } from "./types";

/** Schema-v5 replay reducer. It intentionally projects only legacy lifecycle state. */
export function reduceLegacyV5Event(store: RuntimeStore, event: RuntimeEvent): RuntimeStore {
  let status = store.session.status;
  if (event.type === "SESSION_STARTED") status = "running";
  if (["SESSION_STOPPED", "SESSION_CONTROLLED", "SESSION_STATUS_CHANGED"].includes(event.type)) {
    status = typeof event.payload.status === "string" ? event.payload.status : status;
  }
  const solverId = event.solverId ?? store.session.supervisorSolverId;
  const solversById = solverId && store.solversById[solverId]
    ? { ...store.solversById, [solverId]: { ...store.solversById[solverId], status } }
    : store.solversById;
  return {
    ...store,
    session: { ...store.session, status },
    team: { ...store.team, status },
    solversById,
  };
}
