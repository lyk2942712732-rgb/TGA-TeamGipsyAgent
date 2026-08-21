import { beforeEach, describe, expect, it, vi } from "vitest";

const requestJson = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ requestJson }));

import { loadTGA3Runtime } from "./tga3-runtime";

describe("TGA3 runtime projection", () => {
  beforeEach(() => requestJson.mockReset());

  it("projects real task, agent, blackboard and dialogue contracts into the preserved workbench", async () => {
    requestJson
      .mockResolvedValueOnce({ task: { id: "task-1", title: "demo", scene_id: "pwn", state: "running", blackboard_seq: 1, dialogue_seq: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:02Z" }, agents: [{ agent_id: "worker-openai", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", display_name: "OpenAI Worker", role: "worker", runtime_location: "container", updated_at: "2026-01-01T00:00:02Z" }] })
      .mockResolvedValueOnce({ entries: [{ id: "finding-1", seq: 1, actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "finding", topic: "flag", body: { claim: "找到候选", detail: "已验证" }, created_at: "2026-01-01T00:00:01Z" }] })
      .mockResolvedValueOnce([{ id: "dialogue-1", seq: 1, channel_agent_id: "worker-openai", actor: { agent_id: "system", display_name: "TGA3", role: "system" }, kind: "agent_status", text: "Worker 正在运行", payload: {}, created_at: "2026-01-01T00:00:02Z" }]);

    const store = await loadTGA3Runtime("task-1");
    expect(store.task).toMatchObject({ id: "task-1", name: "demo", mode: "pwn" });
    expect(store.solversById["worker-openai"]).toMatchObject({ status: "running", orchestrationRole: "worker" });
    expect(store.findingsById["finding-1"].title).toBe("找到候选");
    expect(Object.values(store.eventsBySeq).map((event) => event.type)).toEqual(["FINDING_CONFIRMED", "SOLVER_STATUS_CHANGED"]);
  });
});
