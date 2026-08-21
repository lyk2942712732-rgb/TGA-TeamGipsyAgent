import { beforeEach, describe, expect, it, vi } from "vitest";

const requestJson = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ requestJson }));
import { loadTGA3Snapshot } from "./tga3-runtime";

describe("loadTGA3Snapshot", () => {
  beforeEach(() => requestJson.mockReset());
  it("returns the native task, agent, blackboard and dialogue contracts", async () => {
    const task = { id: "task-1", title: "demo", scene_id: "pwn", state: "running", blackboard_seq: 1, dialogue_seq: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:02Z" };
    const agents = [{ agent_id: "worker-openai", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", protocol: "openai_responses", display_name: "OpenAI Worker", role: "worker", runtime_location: "container", updated_at: task.updated_at }];
    const entries = [{ id: "finding-1", seq: 1, actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "finding", topic: "flag", body: { claim: "找到候选" }, created_at: task.updated_at }];
    const dialogue = [{ id: "dialogue-1", seq: 1, channel_agent_id: "worker-openai", actor: { agent_id: "system", display_name: "TGA3", role: "system" }, kind: "agent_status", text: "Worker 正在运行", payload: {}, created_at: task.updated_at }];
    requestJson.mockResolvedValueOnce({ task, agents }).mockResolvedValueOnce({ entries }).mockResolvedValueOnce(dialogue);
    await expect(loadTGA3Snapshot("task-1")).resolves.toEqual({ task, agents, blackboard: entries, dialogue });
    expect(requestJson.mock.calls.map(([url]) => url)).toEqual(["/api/v3/tasks/task-1", "/api/v3/tasks/task-1/blackboard?limit=500", "/api/v3/tasks/task-1/dialogue?limit=500"]);
  });
});
