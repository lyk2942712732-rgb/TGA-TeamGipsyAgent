import { describe, expect, it } from "vitest";
import { normalizeRuntimeSnapshot } from "./normalize";
import { projectLegacyRuntimeView } from "./legacy-view";
import {
  selectActiveSolvers,
  selectConfirmedFindings,
  selectEventsByIntent,
  selectEventsBySolver,
  selectKnowledgeConflicts,
  selectPendingApprovals,
  selectRunnableIntents,
  selectSolverTree,
  selectSupervisor,
  selectTaskBudget,
} from "./selectors";

const v6Snapshot = () => ({
  schema_version: 6,
  task: { id: "task", name: "Multi solver", mode: "ctf", goal: "verify" },
  session: {
    status: "running", supervisor_solver_id: "supervisor", active_solver_count: 2,
    max_active_workers: 2, task_budget_usage: { input_tokens: 30 }, stop_reason: null,
    timestamps: { started_at: "2026-07-30T00:00:00Z" }, turn_count: 2, max_turns: 20,
  },
  team: { task_id: "task", status: "running", supervisor_solver_id: "supervisor", max_active_workers: 2, max_total_solvers: 8, active_solver_count: 2, solver_ids: ["supervisor", "worker-a", "worker-b"], version: 3, timestamps: {} },
  solvers: [
    { task_id: "task", solver_id: "supervisor", definition_id: "supervisor-v1", orchestration_role: "supervisor", specialties: ["planning"], parent_solver_id: null, assigned_intent_id: null, status: "running", current_summary: "coordinate", model_snapshot: { model: "m" }, skill_snapshot: { count: 1 }, tool_policy: { count: 2 }, budget_usage: { input_tokens: 10 }, timestamps: {} },
    { task_id: "task", solver_id: "worker-a", definition_id: "worker-v1", orchestration_role: "worker", specialties: ["web"], parent_solver_id: "supervisor", assigned_intent_id: "intent-a", status: "running", current_summary: "inspect", model_snapshot: {}, skill_snapshot: {}, tool_policy: {}, budget_usage: { input_tokens: 20 }, timestamps: {} },
    { task_id: "task", solver_id: "worker-b", definition_id: "worker-v1", orchestration_role: "worker", specialties: ["binary"], parent_solver_id: "supervisor", assigned_intent_id: "intent-b", status: "completed", current_summary: "done", model_snapshot: {}, skill_snapshot: {}, tool_policy: {}, budget_usage: {}, timestamps: {} },
  ],
  intents: [
    { task_id: "task", intent_id: "intent-a", kind: "investigate", title: "Inspect", objective: "inspect", status: "running", assigned_solver_id: "worker-a", dependencies: [], priority: 2, budget: {}, created_at: "", updated_at: "" },
    { task_id: "task", intent_id: "intent-b", kind: "investigate", title: "Decode", objective: "decode", status: "completed", assigned_solver_id: "worker-b", dependencies: [], priority: 1, budget: {}, created_at: "", updated_at: "" },
    { task_id: "task", intent_id: "intent-c", kind: "report", title: "Report", objective: "report", status: "ready", assigned_solver_id: null, dependencies: ["intent-b"], priority: 0, budget: {}, created_at: "", updated_at: "" },
  ],
  worker_results: [{ result_id: "result-a", solver_id: "worker-a", intent_id: "intent-a", status: "submitted", summary: "partial", artifact_ids: [], evidence_claim_ids: [], knowledge_ids: [], finding_ids: [], limitations: [], budget_usage: {} }],
  global_plan: { version: 3 },
  knowledge: [
    { knowledge_id: "knowledge-a", scope: "task", target_id: null, status: "verified", kind: "fact", content_preview: "known", content_sha256: "a", created_by_solver_id: "worker-a", created_at: "" },
    { knowledge_id: "knowledge-conflict", scope: "task", target_id: null, status: "candidate", kind: "conflict", content_preview: "conflict", content_sha256: "b", created_by_solver_id: "worker-b", created_at: "" },
  ],
  artifacts: [{ artifact_id: "artifact-a", intent_id: "intent-a", kind: "tool_output", media_type: "text/plain", tool: "http", target: "target", sha256: "a", created_at: "" }],
  evidence_claims: [{ claim_id: "claim-a", statement_preview: "proof", artifact_id: "artifact-a", locator: { kind: "line" }, status: "confirmed", created_by_solver_id: "worker-a", reviewed_by_solver_id: "supervisor", created_at: "", reviewed_at: "" }],
  findings: [{ finding_id: "finding-a", title: "Found", description_preview: "confirmed", target: "target", severity: "high", status: "confirmed", evidence_claim_ids: ["claim-a"], created_by_solver_id: "worker-a", created_at: "", reviewed_at: "" }],
  actions: [],
  approvals: [
    { approval_id: "approval-a", solver_id: "worker-a", intent_id: "intent-a", action_id: "action-a", action: { capability: "workspace.write" }, risk: "active", effect: {}, reason: "write", alternatives: ["preview"], deadline: "soon", status: "pending", created_at: "", updated_at: "" },
    { approval_id: "approval-b", solver_id: "worker-b", intent_id: "intent-b", action_id: "action-b", action: { capability: "http.request" }, risk: "active", effect: {}, reason: "request", alternatives: [], deadline: "later", status: "pending", created_at: "", updated_at: "" },
  ],
  retrieval_runs: [{ retrieval_run_id: "retrieval-a", owner_scope: "task", task_id: "task", solver_id: "worker-a", intent_id: "intent-a", index_snapshot_id: "index-a", method: "keyword", query_preview: "query", hit_count: 2, created_at: "" }],
  events: [
    { schema_version: 6, id: "event-1", task_id: "task", seq: 1, type: "INTENT_ASSIGNED", solver_id: "worker-a", intent_id: "intent-a", payload: { schema_version: 1 }, created_at: "" },
    { schema_version: 6, id: "event-2", task_id: "task", seq: 2, type: "SOLVER_COMPLETED", solver_id: "worker-b", intent_id: "intent-b", payload: { schema_version: 1 }, created_at: "" },
  ],
  events_page: { after_seq: 0, next_after_seq: 2, has_more: false }, latest_seq: 2,
});

describe("Phase 10 normalized runtime store", () => {
  it("normalizes a multi-solver v6 snapshot and exposes derived selectors", () => {
    const store = normalizeRuntimeSnapshot(v6Snapshot());
    expect(store.schemaVersion).toBe(6);
    expect(Object.keys(store.solversById)).toHaveLength(3);
    expect(selectSupervisor(store)?.solverId).toBe("supervisor");
    expect(selectActiveSolvers(store).map((item) => item.solverId)).toEqual(["supervisor", "worker-a"]);
    expect(selectSolverTree(store)[0].children.map((item) => item.solver.solverId)).toEqual(["worker-a", "worker-b"]);
    expect(selectRunnableIntents(store).map((item) => item.intentId)).toEqual(["intent-c"]);
    expect(selectPendingApprovals(store)).toHaveLength(2);
    expect(selectTaskBudget(store)).toEqual({ input_tokens: 30 });
    expect(selectEventsBySolver(store, "worker-a")).toHaveLength(1);
    expect(selectEventsByIntent(store, "intent-b")).toHaveLength(1);
    expect(selectConfirmedFindings(store).map((item) => item.findingId)).toEqual(["finding-a"]);
    expect(selectKnowledgeConflicts(store).map((item) => item.knowledgeId)).toEqual(["knowledge-conflict"]);
  });

  it("adapts schema-v5 once at the boundary instead of leaking schema checks", () => {
    const store = normalizeRuntimeSnapshot({
      schema_version: 5,
      task: { id: "legacy", name: "Legacy", mode: "ctf", goal: "replay", schema_version: 5 },
      session: { task_id: "legacy", status: "completed", active_solver_id: "main", turn_count: 4, max_turns: 8 },
      intents: [], artifacts: [], findings: [], memory: [], strategy_cards: [],
      agent_events: [{ schema_version: 5, id: "legacy-event", task_id: "legacy", seq: 1, type: "SESSION_STOPPED", solver_id: "main", payload: { status: "completed" }, created_at: "" }],
      latest_seq: 1,
    });
    expect(store.schemaVersion).toBe(5);
    expect(store.legacy).toBe(true);
    expect(selectSupervisor(store)?.solverId).toBe("main");
    expect(store.eventsBySeq[1].type).toBe("SESSION_STOPPED");
  });

  it("keeps the old Runtime page available through one compatibility view", () => {
    const view = projectLegacyRuntimeView(normalizeRuntimeSnapshot(v6Snapshot()));
    expect(view.task.id).toBe("task");
    expect(view.solvers.map((item) => item.id)).toEqual(["supervisor", "worker-a", "worker-b"]);
    expect(view.latest_seq).toBe(2);
  });
});
