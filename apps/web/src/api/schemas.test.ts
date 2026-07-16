import { describe, expect, it } from "vitest";
import { AgentEventSchema, RuntimeSnapshotSchema } from "./schemas";

describe("AgentEventSchema", () => {
  it("keeps optional payload fields renderable", () => {
    const event = AgentEventSchema.parse({
      id: "evt_optional",
      task_id: "task_1",
      seq: 42,
      type: "SESSION_CONTROLLED",
      payload: { action: "resume", action_id: null, status: "running", future_field: { safe: true } },
      created_at: "2026-07-13T00:00:00Z",
    });

    expect(event.payload["action"]).toBe("resume");
    expect(event.payload["action_id"]).toBeNull();
    expect(event.payload["future_field"]).toEqual({ safe: true });
  });

  it("retains v2 challenge and subagent collaboration summaries", () => {
    const snapshot = RuntimeSnapshotSchema.parse({
      task: { id: "task_1", name: "CTF", mode: "ctf", target: "http://target", scope: ["target"] },
      session: { status: "running", turn_count: 1, max_turns: 48 }, solvers: [],
      challenge: { status: "active", status_reason: "awaiting evidence" },
      subagents: [{ request: { id: "subreq_1", parent_solver_id: "solver_main", role: "recon", objective: "Map routes", hypothesis_ids: [], max_actions: 8 }, solver_id: "solver_recon", status: "completed", output: { coverage_gaps: ["authenticated route"], artifact_ids: [], next_recommendation: "Continue targeted validation" } }],
      board: { hypotheses: [], memory: [] }, actions: [], flags: [], findings: [], artifacts: [], events: [], latest_seq: 0,
    });
    expect(snapshot.challenge.status).toBe("active");
    expect(snapshot.subagents[0].output?.coverage_gaps).toEqual(["authenticated route"]);
  });

  it("keeps an in-flight action with null optional text renderable", () => {
    const snapshot = RuntimeSnapshotSchema.parse({
      task: { id: "task_live", name: "Live", mode: "ctf", target: "http://target", scope: [] },
      session: { status: "running", turn_count: 1, max_turns: 48 },
      solvers: [], challenge: null, subagents: [], board: { hypotheses: [], memory: [] },
      actions: [{ id: "act_running", capability: "http.request", target: null, status: "running", rationale: null, summary: null, artifact_ids: [] }],
      flags: [], findings: [], artifacts: [], events: [], latest_seq: 1,
    });

    expect(snapshot.actions[0].summary).toBe("");
    expect(snapshot.actions[0].target).toBe("");
    expect(snapshot.actions[0].rationale).toBeUndefined();
  });
});
