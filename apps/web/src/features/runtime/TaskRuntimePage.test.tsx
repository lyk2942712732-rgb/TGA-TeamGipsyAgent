import { fireEvent, render, screen, within } from "@testing-library/react";
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
  it("switches shared blackboard entries between details and square tiles", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=blackboard"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    const switcher = screen.getByRole("group", { name: "共享黑板展示方式" });
    fireEvent.click(within(switcher).getByRole("button", { name: "方块" }));
    const tile = screen.getByRole("button", { name: "查看黑板 #2：Finding" });
    fireEvent.click(tile);
    expect(screen.getByRole("dialog", { name: "黑板 #2 详情" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭黑板详情" }));
    expect(screen.queryByRole("dialog", { name: "黑板 #2 详情" })).not.toBeInTheDocument();
    fireEvent.click(within(switcher).getByRole("button", { name: "详情" }));
    expect(screen.getByText("已发现入口")).toBeInTheDocument();
  });
  it("selects an agent and opens its native inspector", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("option", { name: /OpenAI Worker/ }));
    expect(screen.getByRole("heading", { name: "OpenAI Worker" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "活动" }));
    expect(screen.getByText("开始执行")).toBeInTheDocument();
  });
  it("opens a square Finding as a focused message", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=findings"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    const tile = screen.getByRole("button", { name: "查看 Finding #2：已发现入口" });
    fireEvent.click(tile);
    const dialog = screen.getByRole("dialog", { name: "Finding #2 详情" });
    expect(within(dialog).getByRole("heading", { name: "已发现入口" })).toBeInTheDocument();
    expect(within(dialog).getByText("该 Finding 没有补充详情。")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭 Finding 详情" }));
    expect(screen.queryByRole("dialog", { name: "Finding #2 详情" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText("已发现入口")).toBeInTheDocument();
  });
  it("does not show stale errors for a stopped worker", () => {
    useTGA3Runtime.mockReturnValue({
      snapshot: {
        ...snapshot,
        task: { ...snapshot.task, state: "completed" },
        agents: snapshot.agents.map((agent) => agent.agent_id === "worker-openai"
          ? { ...agent, desired_state: "stopped", actual_state: "stopped", last_error: "控制通道意外断开" }
          : agent),
      },
      connection: "live",
      error: null,
      refresh: vi.fn(),
    });
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(screen.queryByText("控制通道意外断开")).not.toBeInTheDocument();
  });
  it("shows the standalone runtime graph with per-agent models and concurrent owner flashes", () => {
    const view = render(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    const graph = screen.getByRole("region", { name: "实时运行图" });
    expect(within(graph).getByText("用户")).toBeInTheDocument();
    expect(within(graph).getByText("Skills")).toBeInTheDocument();
    expect(within(graph).getByText("黑板")).toBeInTheDocument();
    expect(within(graph).getAllByText("gpt")).toHaveLength(2);
    expect(within(graph).queryByText("大模型")).not.toBeInTheDocument();
    expect(within(graph).queryByText(/读取 Skill/)).not.toBeInTheDocument();
    expect(within(graph).getByRole("button", { name: "缩小运行图" })).toBeInTheDocument();
    fireEvent.click(within(graph).getByRole("button", { name: "放大运行图" }));
    expect(within(graph).getByRole("button", { name: "还原运行图" })).toHaveTextContent("125%");
    fireEvent.click(within(graph).getByRole("button", { name: "还原运行图" }));
    expect(within(graph).getByRole("button", { name: "还原运行图" })).toHaveTextContent("100%");

    useTGA3Runtime.mockReturnValue({
      snapshot: {
        ...snapshot,
        dialogue: [...snapshot.dialogue, {
          id: "skill-action",
          seq: 3,
          channel_agent_id: "worker-openai",
          actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" },
          kind: "action_started",
          text: "mcp__blackboard__skills_list",
          payload: { tool: "mcp__blackboard__skills_list" },
          created_at: "2026-01-01T00:01:10Z",
        }],
      },
      connection: "live",
      error: null,
      refresh: vi.fn(),
    });
    view.rerender(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(within(screen.getByRole("region", { name: "实时运行图" })).getByText("OpenAI Worker 读取 Skill")).toHaveClass("interaction");

    useTGA3Runtime.mockReturnValue({
      snapshot: {
        ...snapshot,
        dialogue: [...snapshot.dialogue,
          { id: "skill-action", seq: 3, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "action_started", text: "mcp__blackboard__skills_list", payload: { tool: "mcp__blackboard__skills_list" }, created_at: "2026-01-01T00:01:10Z" },
          { id: "shell-action", seq: 4, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "action_started", text: "id; pwd", payload: { tool: "shell" }, created_at: "2026-01-01T00:01:11Z" },
        ],
      },
      connection: "live",
      error: null,
      refresh: vi.fn(),
    });
    view.rerender(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    const updatedGraph = screen.getByRole("region", { name: "实时运行图" });
    expect(within(updatedGraph).queryByText("OpenAI Worker 读取 Skill")).not.toBeInTheDocument();
    expect(within(updatedGraph).getByText("OpenAI Worker 执行 id; pwd")).toBeInTheDocument();

    useTGA3Runtime.mockReturnValue({
      snapshot: {
        ...snapshot,
        dialogue: [...snapshot.dialogue,
          { id: "skill-action", seq: 3, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "action_started", text: "mcp__blackboard__skills_list", payload: { tool: "mcp__blackboard__skills_list" }, created_at: "2026-01-01T00:01:10Z" },
          { id: "shell-action", seq: 4, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "action_started", text: "id; pwd", payload: { tool: "shell" }, created_at: "2026-01-01T00:01:11Z" },
          { id: "supervisor-action", seq: 5, channel_agent_id: "supervisor", actor: { agent_id: "supervisor", display_name: "Supervisor", role: "supervisor" }, kind: "action_started", text: "review findings", payload: { tool: "review" }, created_at: "2026-01-01T00:01:12Z" },
        ],
      },
      connection: "live",
      error: null,
      refresh: vi.fn(),
    });
    view.rerender(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    const concurrentGraph = screen.getByRole("region", { name: "实时运行图" });
    expect(within(concurrentGraph).getByText("OpenAI Worker 执行 id; pwd")).toBeInTheDocument();
    expect(within(concurrentGraph).getByText("Supervisor review findings")).toBeInTheDocument();
    view.rerender(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(within(screen.getByRole("region", { name: "实时运行图" })).getByText("OpenAI Worker 执行 id; pwd")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "实时运行图" })).getByText("Supervisor review findings")).toBeInTheDocument();
  });

  it("shows and truncates the concrete shell command from the action payload", () => {
    const command = "curl -sS https://target.example/api/v1/debug?token=temporary-token-value --header 'Accept: application/json'";
    useTGA3Runtime.mockReturnValue({
      snapshot: {
        ...snapshot,
        dialogue: [...snapshot.dialogue, {
          id: "long-shell-action",
          seq: 3,
          channel_agent_id: "worker-openai",
          actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" },
          kind: "action_started",
          text: "shell_exec",
          payload: { tool: "shell_exec", input: { command } },
          created_at: "2026-01-01T00:01:10Z",
        }],
      },
      connection: "live",
      error: null,
      refresh: vi.fn(),
    });
    render(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(within(screen.getByRole("region", { name: "实时运行图" })).getByText("OpenAI Worker 执行 curl -sS https://target.example/api/v1/debug?token=tempora…")).toBeInTheDocument();
  });
});
