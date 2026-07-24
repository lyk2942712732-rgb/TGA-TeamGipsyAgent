import { describe, expect, it } from "vitest";
import { AgentEventSchema, RuntimeSnapshotSchema } from "./schemas";

describe("runtime schemas", () => {
  it("accepts StrategyCard runtime state and defaults optional collections", () => {
    const value = RuntimeSnapshotSchema.parse({ task: { id: "task", name: "Task", mode: "ctf", task_entry_url: null, session_input: { prompt: "inspect input", files: [] } }, session: { status: "awaiting_approval", turn_count: 0, max_turns: 8 }, runtime: { strategy_cards: [], memory: [] }, events: [], latest_seq: 0 });
    expect(value.runtime).toEqual({ strategy_cards: [], memory: [] }); expect(value.actions).toEqual([]); expect(value.artifacts).toEqual([]);
    expect(value.task).toMatchObject({ prompt: "inspect input", files: [] });
    expect(value.task).not.toHaveProperty("session_input");
    expect(value.session.status).toBe("awaiting_approval");
  });
  it("preserves unknown event payload fields", () => { const event = AgentEventSchema.parse({ id: "1", seq: 1, type: "FUTURE", payload: { nested: { value: 1 } } }); expect(event.payload).toEqual({ nested: { value: 1 } }); });
  it("accepts historical null provider usage counters", () => {
    const value = RuntimeSnapshotSchema.parse({
      task: { id: "task", name: "Task", mode: "ctf", session_input: { prompt: "inspect", files: [] } },
      session: { status: "running", turn_count: 1, max_turns: 8 }, runtime: { strategy_cards: [], memory: [] }, events: [], latest_seq: 0,
      context_metrics: [{ turn: 1, audit_message_count: 2, working_message_count: 3, working_chars: 128, provider_input_tokens: null, provider_output_tokens: null }],
    });
    expect(value.context_metrics[0]).toMatchObject({ turn: 1, provider_input_tokens: undefined, provider_output_tokens: undefined });
  });
  it("preserves the frozen task model when no solver record is available", () => {
    const value = RuntimeSnapshotSchema.parse({
      task: {
        id: "task", name: "Task", mode: "ctf", session_input: { prompt: "inspect", files: [] },
        model_snapshot: { provider: "openai-compatible", model: "frozen-model", verification_id: "verify_1", verified_at: "2026-07-24T00:00:00Z" },
      },
      session: { status: "paused", turn_count: 0, max_turns: 8 }, runtime: { strategy_cards: [], memory: [] }, events: [], latest_seq: 0,
    });
    expect(value.task.model_snapshot?.model).toBe("frozen-model");
  });
});
