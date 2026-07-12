import { describe, expect, it } from "vitest";
import { AgentEventSchema } from "./schemas";

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
});
