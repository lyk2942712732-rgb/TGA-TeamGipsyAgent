import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AOEQuestion, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import type { TGA3WorkspaceTab } from "./TGA3Workspace";

const mocks = vi.hoisted(() => ({ runtime: vi.fn(), request: vi.fn() }));
vi.mock("../use-tga3-runtime", () => ({ useTGA3Runtime: mocks.runtime }));
vi.mock("../../../api/client", () => ({ apiBase: "http://fixture", requestJson: mocks.request }));
import { AOEWorkspace } from "./AOEWorkspace";
import { TGA3RuntimeGraph } from "./TGA3RuntimeGraph";

const stamp = "2026-11-04T10:00:00Z";
function question(id: string, title: string): AOEQuestion {
  return { question_id: id, parent_task_id: "batch", title, description: `Original ${title} description`,
    source_key: id, difficulty: "easy", connection_info: "http://fixture/target", submission_instructions: "Verify locally",
    status: "running", attempted_by: ["worker-openai"], current_agents: ["worker-openai"], attempts: [],
    flag: null, report_state: "pending", blackboard_seq: 1,
    attachments: [{ id: `file-${id}`, name: "source.zip", media_type: "application/zip", size_bytes: 42, sha256: "0".repeat(64) }] };
}
const parent: TGA3RuntimeSnapshot = {
  task: { id: "batch", title: "AOE batch", scene_id: "aoe", state: "running", blackboard_seq: 1, event_seq: 1, created_at: stamp, updated_at: stamp },
  agents: [{ agent_id: "worker-openai", display_name: "Codex Worker", role: "worker", sdk: "codex_cli", desired_state: "running", actual_state: "running", provider_id: "fixture", model_id: "model", protocol: "openai_responses", runtime_location: "container", updated_at: stamp, metadata: { current_question_id: "q1", current_question_title: "Question One" } }],
  blackboard: [], events: [], aoe: { task_id: "batch", phase: "solo", attempt_seconds: 1200,
    questions: [question("q1", "Question One"), { ...question("q2", "Question Two"), status: "solved", flag: "flag{two}", current_agents: [] }] },
};
const child: TGA3RuntimeSnapshot = { ...parent, aoe: undefined,
  task: { ...parent.task, id: "q1", parent_task_id: "batch", title: "Question One" },
  blackboard: [{ id: "fact", seq: 1, question_id: "q1", actor: { agent_id: "worker-openai", display_name: "Codex Worker", role: "worker" }, kind: "finding", body: { claim: "Only question one's fact" }, created_at: stamp }],
};
function Harness() { const [tab, setTab] = useState<TGA3WorkspaceTab>("blackboard"); return <AOEWorkspace snapshot={parent} tab={tab} onTab={setTab} selectedAgentId="worker-openai" onChanged={vi.fn()} />; }

describe("AOE question workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.runtime.mockImplementation((id: string | null) => ({ snapshot: id === "q1" ? child : null, error: null, refresh: vi.fn() }));
    mocks.request.mockResolvedValue({ events: [], has_more: false });
  });
  it("shows per-question ownership, answer and scoped attachment links, and opens the matching board", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Harness /></MemoryRouter>);
    expect(screen.getByRole("tab", { name: /总览/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "运行图" })).not.toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "题目总览" });
    expect(overview).toHaveTextContent("已尝试"); expect(overview).toHaveTextContent("正在解题");
    expect(overview).toHaveTextContent("flag{two}");
    const row = within(overview).getByRole("button", { name: "Question One" }).closest("article")!;
    await user.click(within(row).getByText(/题面与附件/));
    expect(within(row).getByRole("link", { name: "source.zip" })).toHaveAttribute("href", "http://fixture/api/v3/tasks/batch/questions/q1/files/file-q1");
    await user.click(within(row).getByRole("button", { name: /进入黑板详情/ }));
    expect(screen.getByRole("heading", { name: "Only question one's fact" })).toBeInTheDocument();
    expect(screen.getByLabelText("题目筛选")).toHaveValue("q1");
    await user.click(screen.getByRole("button", { name: "返回总览" }));
    expect(screen.getByRole("region", { name: "题目总览" })).toBeInTheDocument();
  });
  it("requests events by question first and then current agent", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Harness /></MemoryRouter>);
    await user.selectOptions(screen.getByLabelText("题目筛选"), "q1");
    await user.click(screen.getByRole("tab", { name: /执行事件/ }));
    await user.click(screen.getByRole("button", { name: "当前 Agent" }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledWith(expect.stringContaining("question_id=q1&agent_id=worker-openai"), expect.any(Object)));
  });
  it("does not show a previous question's board while the new selection is loading", async () => {
    const user = userEvent.setup();
    mocks.runtime.mockReturnValue({ snapshot: child, error: null, refresh: vi.fn() });
    render(<MemoryRouter initialEntries={["/?question=q1"]}><Harness /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Only question one's fact" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("题目筛选"), "q2");
    expect(screen.queryByRole("heading", { name: "Only question one's fact" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "正在读取本题黑板与记录" })).toBeInTheDocument();
  });
  it("labels the active question on the Worker graph node", () => {
    render(<TGA3RuntimeGraph snapshot={parent} />);
    expect(screen.getByRole("button", { name: "查看 Codex Worker" })).toHaveTextContent("题目：Question One");
  });
  it("can display historical preparation without advertising an obsolete AOE budget", () => {
    const preparing = { ...parent, aoe: { ...parent.aoe!, scout_question_seconds: 300,
      questions: [{ ...question("q1", "Question One"), scout_ready: false, scout_phase: "extracting", status: "preparing", current_agents: [], attempted_by: [] },
        { ...question("q2", "Question Two"), scout_ready: true, status: "queued", current_agents: [] }] } };
    render(<MemoryRouter><AOEWorkspace snapshot={preparing} tab="blackboard" onTab={vi.fn()} selectedAgentId="worker-openai" onChanged={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText("第二轮提取")).toBeInTheDocument();
    expect(screen.getByText("待解题")).toBeInTheDocument();
    expect(screen.getByText(/1 待交接/)).toBeInTheDocument();
    expect(screen.queryByText(/每题侦察/)).not.toBeInTheDocument();
  });
  it("sorts solved questions by solve time and remaining questions by registration time", () => {
    const data = { ...parent, aoe: { ...parent.aoe!, scout_mode: "collection_only", collection_complete: false,
      questions: [
        { ...question("old", "Older unsolved"), registered_at: 10 },
        { ...question("s1", "Earlier solved"), status: "solved", solved_at: 100, registered_at: 40 },
        { ...question("new", "Newest unsolved"), registered_at: 50 },
        { ...question("s2", "Latest solved"), status: "solved", solved_at: 200, registered_at: 20 },
      ] } };
    render(<MemoryRouter><AOEWorkspace snapshot={data} tab="blackboard" onTab={vi.fn()} selectedAgentId={null} onChanged={vi.fn()} /></MemoryRouter>);
    const panel = screen.getByRole("region", { name: "题目总览" });
    expect([...panel.querySelectorAll(".aoe-question-title")].map(node => node.textContent)).toEqual([
      "Latest solved", "Earlier solved", "Newest unsolved", "Older unsolved",
    ]);
    expect(screen.getByText(/收集题目与解题并行/)).toBeInTheDocument();
    expect(screen.getByText(/Scout 仅收集题面与附件/)).toBeInTheDocument();
  });
});
