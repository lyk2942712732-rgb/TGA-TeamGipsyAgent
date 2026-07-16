import { describe, expect, it } from "vitest";

import { applyRuntimeEvent, boardAtSeq, mergeEvents } from "./event-reducer";
import type { RuntimeSnapshot } from "./event-types";

const event = (seq: number, type: string) => ({ id: `evt_${seq}`, task_id: "task", seq, type, payload: {}, created_at: "2026-01-01T00:00:00Z" });
const snapshot = (): RuntimeSnapshot => ({ task: { id: "task", name: "task", mode: "ctf", target: "http://target", scope: ["target"] }, session: { status: "running", turn_count: 0, max_turns: 48 }, solvers: [], challenge: { status: "active", status_reason: "" }, subagents: [], board: { hypotheses: [], memory: [] }, actions: [], flags: [], findings: [], artifacts: [], events: [event(1, "SESSION_STARTED")], latest_seq: 1 });

describe("mergeEvents", () => {
  it("deduplicates reconnect events and retains seq order", () => {
    const result = mergeEvents(snapshot(), [event(3, "ACTION_FINISHED"), event(2, "ACTION_STARTED"), event(1, "SESSION_STARTED")]);
    expect(result.events.map((item) => item.seq)).toEqual([1, 2, 3]);
    expect(result.latest_seq).toBe(3);
  });

  it("keeps an ACTION_PROPOSED action in the unexecuted state", () => {
    const result = applyRuntimeEvent(snapshot(), { ...event(2, "ACTION_PROPOSED"), payload: { action_id: "act_1", capability: "http.request", target: "http://target" } });
    expect(result.actions[0].status).toBe("proposed");
  });

  it("tracks approval and solver lifecycle events without treating approval as execution", () => {
    const proposed = applyRuntimeEvent(snapshot(), { ...event(2, "ACTION_PROPOSED"), payload: { action_id: "act_1", capability: "http.request" } });
    const approved = applyRuntimeEvent(proposed, { ...event(3, "ACTION_APPROVED"), payload: { action_id: "act_1" } });
    expect(approved.actions[0].status).toBe("approved");

    const started = applyRuntimeEvent(approved, { ...event(4, "SOLVER_STARTED"), solver_id: "solver_recon", payload: { role: "recon", model_name: "model-a" } });
    const stopped = applyRuntimeEvent(started, { ...event(5, "SOLVER_STOPPED"), solver_id: "solver_recon", payload: { status: "completed" } });
    expect(stopped.solvers).toMatchObject([{ id: "solver_recon", role: "recon", status: "completed", model_name: "model-a" }]);
  });

  it("uses the latest persisted board snapshot at the replay cursor", () => {
    const current = snapshot();
    current.events.push(
      { ...event(2, "BOARD_SNAPSHOT"), payload: { board: { hypotheses: [{ id: "old" }], memory: [] } as unknown as RuntimeSnapshot["board"] } },
      { ...event(4, "BOARD_SNAPSHOT"), payload: { board: { hypotheses: [{ id: "new" }], memory: [{ id: "mem" }] } as unknown as RuntimeSnapshot["board"] } },
    );
    expect(boardAtSeq(current, 3).board.hypotheses[0].id).toBe("old");
    expect(boardAtSeq(current, 5).board.hypotheses[0].id).toBe("new");
    expect(boardAtSeq(snapshot(), 1).available).toBe(false);
  });
});
