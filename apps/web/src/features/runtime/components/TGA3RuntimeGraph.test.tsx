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
    for (const name of ["任务输入", "黑板", "Skills", "CTF 知识库", "报告"]) expect(within(graph).getAllByText(name).length).toBeGreaterThan(0);
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
    expect(toolActivity(event("5", "worker-claude", { payload: { tool: "mcp__des-ctf-knowledge__find_technique", input: { signal: "PNG CRC" } } }))).toMatchObject({ from: "knowledge", to: "agent:worker-claude", shortLabel: "检索知识库" });
    expect(toolActivity(event("6", "worker-openai", { text: "find_technique", payload: { tool: "mcp_tool_call", server: "des-ctf-knowledge" } }))).toMatchObject({ from: "knowledge", to: "agent:worker-openai" });
  });
  it("routes ctf42 calls from both native CLI formats to knowledge", () => {
    expect(toolActivity(event("ctf42-claude", "worker-claude", { payload: { tool: "mcp__ctf42__list_techniques", input: { query: "heap" } } }))).toMatchObject({ from: "knowledge", to: "agent:worker-claude" });
    expect(toolActivity(event("ctf42-codex", "worker-openai", { payload: { tool: "mcp_tool_call", server: "ctf42" } }))).toMatchObject({ from: "knowledge", to: "agent:worker-openai" });
  });
  it("shows local knowledge details and routes local chapter calls to knowledge", () => {
    const base = snapshot();
    base.task.metadata = { knowledge_source: "local" };
    render(<TGA3RuntimeGraph snapshot={base} />);
    expect(screen.getByText("CTF Wiki · 本地章节 / 全文检索")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /CTF 知识库/ }));
    expect(screen.getByRole("link", { name: "查看 CTF Wiki 来源" })).toHaveAttribute("href", "https://github.com/ctf-wiki/ctf-wiki");
    expect(toolActivity(event("wiki-claude", "worker-claude", { payload: { tool: "mcp__ctf_wiki__read_document" } }))).toMatchObject({ from: "knowledge", to: "agent:worker-claude" });
    expect(toolActivity(event("wiki-codex", "worker-openai", { payload: { tool: "mcp_tool_call", server: "ctf_wiki" } }))).toMatchObject({ from: "knowledge", to: "agent:worker-openai" });
  });
  it("opens knowledge details and highlights only actual retrieval events", () => {
    const base = snapshot(), view = render(<TGA3RuntimeGraph snapshot={base} />);
    const node = screen.getByRole("button", { name: /CTF 知识库/ });
    expect(node).toHaveAttribute("data-active", "false");
    fireEvent.click(node);
    expect(screen.getByRole("link", { name: "查看知识库与接入说明" })).toHaveAttribute("href", "https://ctf42.online/about/");
    const read = event("knowledge-1", "worker-openai", { payload: { tool: "mcp__ctf42__search_challenges", input: { query: "PNG IHDR CRC" } } });
    view.rerender(<TGA3RuntimeGraph snapshot={{ ...base, events: [read] }} />);
    expect(node).toHaveAttribute("data-active", "true");
    expect(screen.getAllByText("Codex Worker 检索 CTF 知识库").length).toBeGreaterThan(0);
    act(() => vi.advanceTimersByTime(2600));
    expect(node).toHaveAttribute("data-active", "false");
  });
});
