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
  it("preserves the frozen Skill bundle for runtime audit UI", () => {
    const value = RuntimeSnapshotSchema.parse({
      task: {
        id: "task", name: "Task", mode: "ctf", session_input: { prompt: "inspect", files: [] },
        skill_bundle_snapshot: {
          schema_version: 1, selector: "task-skill-selector-v1:test", query_summary: "inspect web",
          total_chars: 11,
          skills: [{ name: "web-recon", version: "1", origin: "builtin", modes: ["ctf"], capabilities: ["http.request"], tags: ["web"], body: "skill body!", content_sha256: "a".repeat(64), score: 360, selection_reasons: ["任务特征匹配：web"] }],
        },
      },
      session: { status: "running", turn_count: 0, max_turns: 8 }, runtime: { strategy_cards: [], memory: [] }, events: [], latest_seq: 0,
    });
    expect(value.task.skill_bundle_snapshot?.skills[0]).toMatchObject({ name: "web-recon", origin: "builtin", selection_reasons: ["任务特征匹配：web"] });
  });
});
