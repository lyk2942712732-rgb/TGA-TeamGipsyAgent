import { describe, expect, it } from "vitest";
import { normalizeRuntimeSnapshot } from "./normalize";
import { mergeRuntimeEvents, reduceRuntimeEvent } from "./reducer";
import type { RuntimeEvent } from "./types";

const base = () => normalizeRuntimeSnapshot({
  schema_version: 6,
  task: { id: "task", name: "Task", mode: "ctf" },
  session: { status: "running", supervisor_solver_id: "supervisor", active_solver_count: 1, max_active_workers: 2, task_budget_usage: {}, stop_reason: null, timestamps: {}, turn_count: 0, max_turns: 20 },
  team: { task_id: "task", status: "running", supervisor_solver_id: "supervisor", max_active_workers: 2, max_total_solvers: 8, active_solver_count: 1, solver_ids: ["supervisor"], version: 1, timestamps: {} },
  solvers: [{ task_id: "task", solver_id: "supervisor", definition_id: "supervisor", orchestration_role: "supervisor", specialties: [], parent_solver_id: null, assigned_intent_id: null, status: "running", current_summary: "", model_snapshot: {}, skill_snapshot: {}, capability_binding: {}, budget_usage: {}, timestamps: {} }],
  intents: [], worker_results: [], global_plan: null, knowledge: [], artifacts: [], evidence_claims: [], findings: [], actions: [], approvals: [], retrieval_runs: [], events: [], events_page: { after_seq: 0, next_after_seq: 0, has_more: false }, latest_seq: 0,
});
const event = (seq: number, type: string, payload: Record<string, unknown> = {}, solverId: string | null = null, intentId: string | null = null): RuntimeEvent => ({ schemaVersion: 6, id: `event-${seq}`, taskId: "task", seq, type, solverId, intentId, payload: { schema_version: 1, ...payload }, createdAt: "" });

describe("Phase 10 runtime event reducer", () => {
  it("is idempotent, detects sequence gaps, and tolerates unknown events", () => {
    const first = reduceRuntimeEvent(base(), event(1, "FUTURE_EVENT", { value: true }));
    expect(first.gap).toBe(false);
    expect(first.state.latestSeq).toBe(1);
    expect(reduceRuntimeEvent(first.state, event(1, "FUTURE_EVENT")).state).toBe(first.state);
    const gap = reduceRuntimeEvent(first.state, event(3, "SOLVER_PAUSED", { reason: "operator" }, "supervisor"));
    expect(gap.gap).toBe(true);
    expect(gap.state).toBe(first.state);
  });

  it("updates multiple Solver, Intent, result, approval and completion entities", () => {
    const result = mergeRuntimeEvents(base(), [
      event(1, "SOLVER_CREATED", { solver_id: "worker", definition_id: "worker-v1", orchestration_role: "worker", parent_solver_id: "supervisor" }, "worker"),
      event(2, "INTENT_CREATED", { intent_id: "intent", title: "Inspect", objective: "inspect", status: "ready" }, null, "intent"),
      event(3, "INTENT_ASSIGNED", { intent_id: "intent", solver_id: "worker" }, "worker", "intent"),
      event(4, "INTENT_CLAIMED", { intent_id: "intent", solver_id: "worker" }, "worker", "intent"),
      event(5, "WORKER_RESULT_SUBMITTED", { worker_result_id: "result", intent_id: "intent", summary: "done" }, "worker", "intent"),
      event(6, "APPROVAL_REQUESTED", { approval_id: "approval", action_id: "action", reason: "write", status: "pending" }, "worker", "intent"),
      event(7, "EVIDENCE_CLAIM_CREATED", { evidence_claim_id: "claim", artifact_id: "artifact", statement_preview: "proof" }, "worker", "intent"),
      event(8, "EVIDENCE_CLAIM_REVIEWED", { evidence_claim_id: "claim", status: "confirmed" }, "supervisor", "intent"),
      event(9, "RETRIEVAL_COMPLETED", { retrieval_run_id: "retrieval", index_snapshot_id: "index", hit_count: 2 }, "worker", "intent"),
      event(10, "PLAN_UPDATED", { old_version: 1, new_version: 2, operation: "merge" }, "supervisor"),
      event(11, "INTENT_COMPLETED", { intent_id: "intent", status: "completed" }, "worker", "intent"),
      event(12, "SOLVER_COMPLETED", { summary: "complete" }, "worker", "intent"),
      event(13, "TASK_COMPLETION_ACCEPTED", { proposal_id: "proposal" }, "supervisor"),
    ]);
    expect(result.gap).toBe(false);
    expect(result.state.solversById.worker).toMatchObject({ status: "completed", assignedIntentId: "intent" });
    expect(result.state.intentsById.intent).toMatchObject({ status: "completed", assignedSolverId: "worker" });
    expect(result.state.workerResultsById.result.summary).toBe("done");
    expect(result.state.approvalsById.approval.status).toBe("pending");
    expect(result.state.evidenceById.claim.status).toBe("confirmed");
    expect(result.state.retrievalById.retrieval.hitCount).toBe(2);
    expect(result.state.globalPlan?.version).toBe(2);
    expect(result.state.session.status).toBe("completed");
  });

  it("does not let an older entity version overwrite a newer projection", () => {
    const newerEvent = { ...event(1, "SOLVER_PAUSED", { reason: "new" }, "supervisor"), payload: { schema_version: 1, version: 2 } };
    const newer = reduceRuntimeEvent(base(), newerEvent).state;
    const stale = { ...event(2, "SOLVER_STARTED", {}, "supervisor"), payload: { schema_version: 1, version: 1 } };
    expect(reduceRuntimeEvent(newer, stale).state.solversById.supervisor.status).toBe("paused");
  });

  it("retains only the newest 500 contiguous events", () => {
    const events = Array.from({ length: 501 }, (_, index) => event(index + 1, "FUTURE_EVENT"));
    const result = mergeRuntimeEvents(base(), events);
    expect(Object.keys(result.state.eventsBySeq).map(Number).sort((a, b) => a - b)).toEqual(
      Array.from({ length: 500 }, (_, index) => index + 2),
    );
  });
});
