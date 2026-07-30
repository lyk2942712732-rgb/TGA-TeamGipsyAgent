import type { RuntimeEvent, RuntimeFinding, RuntimeIntent, RuntimeKnowledgeItem, RuntimeSolver, RuntimeStore, SolverTreeNode } from "./types";

const ACTIVE = new Set(["created", "queued", "ready", "running", "waiting", "awaiting_approval"]);

export const selectSupervisor = (store: RuntimeStore): RuntimeSolver | null =>
  (store.session.supervisorSolverId ? store.solversById[store.session.supervisorSolverId] : undefined)
  ?? Object.values(store.solversById).find((solver) => solver.orchestrationRole === "supervisor")
  ?? null;

export const selectActiveSolvers = (store: RuntimeStore): RuntimeSolver[] =>
  Object.values(store.solversById).filter((solver) => ACTIVE.has(solver.status));

export function selectSolverTree(store: RuntimeStore): SolverTreeNode[] {
  const nodes = Object.fromEntries(Object.values(store.solversById).map((solver) => [solver.solverId, { solver, children: [] as SolverTreeNode[] }]));
  const roots: SolverTreeNode[] = [];
  for (const node of Object.values(nodes)) {
    const parent = node.solver.parentSolverId ? nodes[node.solver.parentSolverId] : undefined;
    if (parent && parent !== node) parent.children.push(node); else roots.push(node);
  }
  const sort = (values: SolverTreeNode[]): SolverTreeNode[] => values.sort((a, b) => a.solver.solverId.localeCompare(b.solver.solverId)).map((node): SolverTreeNode => ({ ...node, children: sort(node.children) }));
  return sort(roots);
}

export const selectRunnableIntents = (store: RuntimeStore): RuntimeIntent[] =>
  Object.values(store.intentsById).filter((intent) =>
    ["pending", "ready", "queued"].includes(intent.status)
    && intent.dependencies.every((id) => store.intentsById[id]?.status === "completed")
  ).sort((a, b) => b.priority - a.priority || a.intentId.localeCompare(b.intentId));

export const selectPendingApprovals = (store: RuntimeStore) =>
  Object.values(store.approvalsById).filter((approval) => approval.status === "pending");

export const selectTaskBudget = (store: RuntimeStore) => store.session.taskBudgetUsage;

export const selectEventsBySolver = (store: RuntimeStore, solverId: string): RuntimeEvent[] =>
  orderedEvents(store).filter((event) => event.solverId === solverId);

export const selectEventsByIntent = (store: RuntimeStore, intentId: string): RuntimeEvent[] =>
  orderedEvents(store).filter((event) => event.intentId === intentId);

export const selectLatestEventBySolver = (store: RuntimeStore, solverId: string): RuntimeEvent | null => {
  const values = selectEventsBySolver(store, solverId);
  return values[values.length - 1] ?? null;
};

export const selectPendingApprovalsBySolver = (store: RuntimeStore, solverId: string) =>
  selectPendingApprovals(store).filter((approval) => approval.solverId === solverId);

export const selectConfirmedFindings = (store: RuntimeStore): RuntimeFinding[] =>
  Object.values(store.findingsById).filter((finding) => finding.status === "confirmed");

export const selectKnowledgeConflicts = (store: RuntimeStore): RuntimeKnowledgeItem[] =>
  Object.values(store.knowledgeById).filter((item) => item.kind === "conflict" || item.status === "conflict");

export const orderedEvents = (store: RuntimeStore): RuntimeEvent[] =>
  Object.values(store.eventsBySeq).sort((a, b) => a.seq - b.seq);
