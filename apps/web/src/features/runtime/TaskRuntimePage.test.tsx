import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TGA3RuntimeSnapshot } from "../../runtime/tga3-runtime";

const useTGA3Runtime = vi.hoisted(() => vi.fn());
vi.mock("./use-tga3-runtime", () => ({ useTGA3Runtime: (...args: unknown[]) => useTGA3Runtime(...args) }));
vi.mock("../../api/tga3-tasks", async (original) => ({ ...await original<typeof import("../../api/tga3-tasks")>(), fetchAgentModelOptions: vi.fn(async () => ({ scene_id: "pwn", agents: [], models: [] })) }));
import { TaskRuntimePage } from "./TaskRuntimePage";

const snapshot: TGA3RuntimeSnapshot = {
  task: { id: "task", title: "Native TGA3 task", scene_id: "pwn", state: "running", blackboard_seq: 3, event_seq: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z" },
  agents: [
    { agent_id: "worker-claude", sdk: "codex_cli", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", protocol: "openai_responses", display_name: "Claude Worker", role: "worker", runtime_location: "container", updated_at: "2026-01-01T00:01:00Z" },
    { agent_id: "worker-openai", sdk: "codex_cli", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "gpt", protocol: "openai_responses", display_name: "OpenAI Worker", role: "worker", runtime_location: "container", updated_at: "2026-01-01T00:01:00Z" },
  ],
  blackboard: [
    { id: "prompt", seq: 1, actor: { agent_id: "user", display_name: "用户", role: "user" }, kind: "user_prompt", topic: "initial", body: { text: "拿到 flag" }, created_at: "2026-01-01T00:00:00Z" },
    { id: "finding", seq: 2, actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "finding", topic: "flag", body: { claim: "已发现入口" }, created_at: "2026-01-01T00:01:00Z" },
    { id: "intel", seq: 3, actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "intel", topic: "stack", body: { claim: "确认目标使用 Flask", detail: "响应头与错误页已经交叉验证" }, created_at: "2026-01-01T00:01:10Z" },
  ],
  events: [
    { id: "status", seq: 1, channel_agent_id: "worker-openai", actor: { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker" }, kind: "agent_status", text: "开始执行", payload: {}, created_at: "2026-01-01T00:00:30Z" },
    { id: "progress", seq: 2, channel_agent_id: "worker-claude", actor: { agent_id: "worker-claude", display_name: "Claude Worker", role: "worker" }, kind: "blackboard_progress", text: "黑板已有新 Finding", payload: {}, created_at: "2026-01-01T00:01:00Z" },
  ],
};

describe("TaskRuntimePage", () => {
  beforeEach(() => useTGA3Runtime.mockReturnValue({ snapshot, connection: "live", error: null, refresh: vi.fn() }));
  it("renders only native TGA3 runtime concepts", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Native TGA3 task" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "实时运行图" })).toBeInTheDocument();
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
  it("renders and filters Intel as a first-class blackboard kind", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime?tab=blackboard"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    fireEvent.change(screen.getByRole("combobox", { name: "黑板类型" }), { target: { value: "intel" } });
    expect(screen.getByRole("heading", { name: "确认目标使用 Flask" })).toBeInTheDocument();
    expect(screen.queryByText("已发现入口")).not.toBeInTheDocument();
  });
  it("selects an agent and opens its native inspector", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "查看 OpenAI Worker" }));
    expect(screen.getByRole("heading", { name: "OpenAI Worker", level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "对话" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/添加提示/)).not.toBeInTheDocument();
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

});
