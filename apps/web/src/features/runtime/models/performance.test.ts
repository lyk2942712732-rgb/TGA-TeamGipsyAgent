import { describe, expect, it } from "vitest";
import { normalizeRuntimeSnapshot } from "./normalize";
import { mergeRuntimeEvents } from "./reducer";
import type { RuntimeEvent } from "./types";

describe("Phase 12 frontend event baseline", () => {
  it("reduces 10k sequential events into the bounded store", () => {
    const store = normalizeRuntimeSnapshot({
      schema_version: 6,
      task: { id: "perf", name: "Performance", mode: "ctf" },
      session: { status: "running", supervisor_solver_id: "supervisor", active_solver_count: 1, max_active_workers: 2, task_budget_usage: {}, timestamps: {}, turn_count: 0, max_turns: 20 },
      team: { task_id: "perf", status: "running", supervisor_solver_id: "supervisor", max_active_workers: 2, max_total_solvers: 8, active_solver_count: 1, solver_ids: ["supervisor"], version: 1, timestamps: {} },
      solvers: [{ task_id: "perf", solver_id: "supervisor", definition_id: "task-supervisor", orchestration_role: "supervisor", specialties: [], parent_solver_id: null, assigned_intent_id: null, status: "running", current_summary: "", model_snapshot: {}, skill_snapshot: {}, tool_policy: {}, budget_usage: {}, timestamps: {} }],
      intents: [], worker_results: [], global_plan: null, knowledge: [], artifacts: [], evidence_claims: [], findings: [], actions: [], approvals: [], retrieval_runs: [], events: [], events_page: { after_seq: 0, next_after_seq: 0, has_more: false }, latest_seq: 0,
    });
    const events: RuntimeEvent[] = Array.from({ length: 10_000 }, (_, index) => ({
      schemaVersion: 6, id: `perf-${index + 1}`, taskId: "perf", seq: index + 1,
      type: "BENCHMARK_EVENT", solverId: "supervisor", intentId: null,
      payload: { payload_version: 1, index }, createdAt: "2026-07-30T00:00:00Z",
    }));
    const started = performance.now();
    const result = mergeRuntimeEvents(store, events);
    const elapsed = performance.now() - started;
    console.info(`phase12_frontend_10k_event_ms=${elapsed.toFixed(3)}`);
    expect(result.gap).toBe(false);
    expect(result.state.latestSeq).toBe(10_000);
    expect(Object.keys(result.state.eventsBySeq)).toHaveLength(500);
    expect(elapsed).toBeLessThan(5_000);
  });
});
