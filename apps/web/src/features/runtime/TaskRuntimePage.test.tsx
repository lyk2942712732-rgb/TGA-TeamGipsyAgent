import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TGA3RuntimeSnapshot } from "../../runtime/tga3-runtime";

const useTGA3Runtime = vi.hoisted(() => vi.fn());
vi.mock("./use-tga3-runtime", () => ({ useTGA3Runtime: (...args: unknown[]) => useTGA3Runtime(...args) }));
vi.mock("../../api/tga3-tasks", async (original) => ({ ...await original<typeof import("../../api/tga3-tasks")>(), fetchAgentModelOptions: vi.fn(async () => ({ scene_id: "pwn", agents: [], models: [] })) }));
import { TaskRuntimePage } from "./TaskRuntimePage";

const snapshot: TGA3RuntimeSnapshot = {
  task: { id: "task", title: "Native TGA3 task", scene_id: "pwn", state: "running", blackboard_seq: 2, dialogue_seq: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z" },
  agents: [
    { agent_id: "supervisor", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", protocol: "openai_responses", display_name: "Supervisor", role: "supervisor", runtime_location: "host", updated_at: "2026-01-01T00:01:00Z" },
    { agent_id: "worker-openai", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", protocol: "openai_responses", display_name: "OpenAI Worker", role: "worker", runtime_location: "container", updated_at: "2026-01-01T00:01:00Z" },
  ],
  blackboard: [
    { id: "prompt", seq: 1, actor: { agent_id: "user", display_name: "用户", role: "user" }, kind: "user_prompt", topic: "initial", body: { text: "拿到 flag" }, created_at: "2026-01-01T00:00:00Z" },
    { id: "finding", seq: 2, actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "finding", topic: "flag", body: { claim: "已发现入口" }, created_at: "2026-01-01T00:01:00Z" },
  ],
  dialogue: [
    { id: "status", seq: 1, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "agent_status", text: "开始执行", payload: {}, created_at: "2026-01-01T00:00:30Z" },
    { id: "progress", seq: 2, channel_agent_id: "supervisor", actor: { agent_id: "supervisor", display_name: "Supervisor", role: "supervisor" }, kind: "blackboard_progress", text: "黑板已有新 Finding", payload: {}, created_at: "2026-01-01T00:01:00Z" },
  ],
};

describe("TaskRuntimePage", () => {
  beforeEach(() => useTGA3Runtime.mockReturnValue({ snapshot, connection: "live", error: null, refresh: vi.fn() }));
  it("renders only native TGA3 runtime concepts", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Native TGA3 task" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "任务 Agent" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /共享黑板/ })).toBeInTheDocument();
    expect(screen.getByText("已发现入口")).toBeInTheDocument();
  });
  it("selects an agent and opens its native inspector", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("option", { name: /OpenAI Worker/ }));
    expect(screen.getByRole("heading", { name: "OpenAI Worker" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "活动" }));
    expect(screen.getByText("开始执行")).toBeInTheDocument();
  });
});
