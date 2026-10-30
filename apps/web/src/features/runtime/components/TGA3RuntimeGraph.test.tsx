import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TGA3RuntimeSnapshot, TGA3TaskEvent } from "../../../runtime/tga3-runtime";
import { currentAgentAction, runtimeActivityEvents, toolActivity } from "../runtime-activity";
import { TGA3RuntimeGraph } from "./TGA3RuntimeGraph";

const now = "2026-10-30T05:30:00Z";
function snapshot(): TGA3RuntimeSnapshot {
  return { task: { id: "task", title: "CTF", scene_id: "pwn", state: "running", blackboard_seq: 8, event_seq: 0, created_at: now, updated_at: now }, blackboard: [], events: [], agents: ["scout", "worker-openai", "worker-claude", "reporter"].map(id => ({ agent_id: id, sdk: id === "worker-claude" ? "claude_cli" : "codex_cli", desired_state: "running", actual_state: id === "scout" ? "completed" : id === "reporter" ? "created" : "running", provider_id: "test", model_id: "model-test", protocol: id === "worker-claude" ? "anthropic" : "openai_responses", display_name: id === "worker-openai" ? "Codex Worker" : id === "worker-claude" ? "Claude Worker" : id === "scout" ? "Scout" : "Reporter", role: id === "scout" ? "scout" : id === "reporter" ? "reporter" : "worker", runtime_location: "container", updated_at: now })) };
}
function event(id: string, owner = "worker-openai", input: Partial<TGA3TaskEvent> = {}): TGA3TaskEvent { return { id, seq: Number(id.replace(/\D/g, "")) || 1, channel_agent_id: owner, actor: { agent_id: owner, display_name: owner === "worker-openai" ? "Codex Worker" : "Claude Worker", role: "worker" }, kind: "action_started", text: "Bash", payload: { tool: "Bash", tool_use_id: id, input: { command: "python verify.py" } }, created_at: now, ...input }; }
describe("live collaboration graph", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(now)); });
  afterEach(() => vi.useRealTimers());
  it("keeps the workflow resources visible and makes agent selection and collapse usable", () => {
    const select = vi.fn(); render(<TGA3RuntimeGraph snapshot={snapshot()} onSelectAgent={select} />);
    const graph = screen.getByRole("region", { name: "实时运行图" });
    for (const name of ["任务输入", "黑板", "Skills", "报告"]) expect(within(graph).getAllByText(name).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "查看 Claude Worker" })); expect(select).toHaveBeenCalledWith("worker-claude");
    fireEvent.click(screen.getByRole("checkbox", { name: "模型信息" })); expect(screen.getAllByText("model-test")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "收起运行图" })); expect(screen.queryByRole("button", { name: "查看 Claude Worker" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开运行图" })); expect(screen.getByRole("button", { name: "查看 Claude Worker" })).toBeInTheDocument();
  });
  it("shows concurrent actions and replaces each owner's flash independently", () => {
    const base = snapshot(), view = render(<TGA3RuntimeGraph snapshot={base} />);
    const first = event("call-1"), other = event("call-2", "worker-claude", { text: "Read", payload: { tool: "Read", input: { file_path: "/skills/ctf-web/SKILL.md" } } });
    view.rerender(<TGA3RuntimeGraph snapshot={{ ...base, events: [first, other] }} />);
    expect(screen.getByText("Codex Worker 执行 python verify.py")).toBeInTheDocument(); expect(screen.getByText("Claude Worker 读取 Skill")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(100));
    view.rerender(<TGA3RuntimeGraph snapshot={{ ...base, events: [first, other, event("call-3", "worker-openai", { text: "pwd", payload: { tool: "shell", command: "pwd" } })] }} />);
    expect(screen.queryByText("Codex Worker 执行 pwd")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(100)); expect(screen.getByText("Codex Worker 执行 pwd")).toBeInTheDocument(); expect(screen.getByText("Claude Worker 读取 Skill")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2600)); expect(screen.queryByText("Codex Worker 执行 pwd")).not.toBeInTheDocument();
  });
  it("uses acknowledged per-worker cursors and does not replay unchanged reads", () => {
    const base = snapshot(), view = render(<TGA3RuntimeGraph snapshot={base} />);
    const withRead = { ...base, agents: base.agents.map(a => a.agent_id === "worker-openai" ? { ...a, metadata: { blackboard_cursor: 8, blackboard_read_at: Date.now() / 1000 } } : a) };
    view.rerender(<TGA3RuntimeGraph snapshot={withRead} />); expect(screen.getByText("Codex Worker 同步黑板至 #8")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2600)); view.rerender(<TGA3RuntimeGraph snapshot={{ ...withRead }} />); expect(screen.queryByText("Codex Worker 同步黑板至 #8")).not.toBeInTheDocument();
    expect(runtimeActivityEvents(withRead).filter(x => x.activity?.from === "blackboard")).toHaveLength(1);
  });
  it("does not replay old events or keep moving connections after completion", () => {
    const base = snapshot(), old = event("call-1", "worker-openai", { created_at: "2026-10-30T05:00:00Z" });
    const view = render(<TGA3RuntimeGraph snapshot={{ ...base, events: [old] }} />); expect(document.querySelector(".collab-now")).toHaveTextContent("等待新的动作");
    view.rerender(<TGA3RuntimeGraph snapshot={{ ...base, events: [old, event("call-2")] }} />); expect(screen.getByText("Codex Worker 执行 python verify.py")).toBeInTheDocument();
    view.rerender(<TGA3RuntimeGraph snapshot={{ ...base, task: { ...base.task, state: "completed" }, events: [old, event("call-2")] }} />); expect(document.querySelector(".collab-now")).toHaveTextContent("本次运行交互已完成");
  });
  it("keeps a running tool across unrelated completions and retains its command after completion", () => {
    const base = snapshot(), first = event("call-1"), second = event("call-2");
    base.events = [first, second, { ...second, id: "done-2", seq: 3, kind: "action_completed", payload: { tool: "Bash", tool_use_id: "call-2" } }];
    expect(currentAgentAction(base, base.agents[1])).toEqual(expect.objectContaining({ pending: true, detail: "python verify.py" }));
    base.events.push({ ...first, id: "done-1", seq: 4, kind: "action_completed", payload: { tool: "Bash", tool_use_id: "call-1" } });
    expect(currentAgentAction(base, base.agents[1])).toEqual(expect.objectContaining({ pending: false, detail: "python verify.py" }));
    base.agents[1].actual_state = "paused"; expect(currentAgentAction(base, base.agents[1])).toBeNull();
  });
  it("recognizes CLI command payloads and read/write direction", () => {
    const read = toolActivity(event("1", "worker-openai", { payload: { tool: "mcp__blackboard__blackboard_sync" } })); expect(read).toMatchObject({ from: "blackboard", to: "agent:worker-openai" });
    const write = toolActivity(event("2", "worker-openai", { payload: { tool: "mcp__blackboard__blackboard_publish" } })); expect(write).toMatchObject({ from: "agent:worker-openai", to: "blackboard" });
    expect(toolActivity(event("3", "worker-openai", { payload: { tool: "command_execution", command: "id" } }))?.detail).toBe("id");
    expect(toolActivity(event("4", "worker-claude", { payload: { tool: "Read", input: { file_path: "/skills/pkg/requests.md" } } }))).toMatchObject({ from: "skills", to: "agent:worker-claude" });
  });
});
