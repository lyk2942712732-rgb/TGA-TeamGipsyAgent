import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AOEAttempt, AOEQuestion, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { TGA3AgentInspector } from "./TGA3AgentInspector";

const stamp = "2026-11-06T10:00:00Z";
const agent = { agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker", sdk: "codex_cli", actual_state: "running", desired_state: "running", provider_id: "fixture", model_id: "fixture", protocol: "openai_responses", runtime_location: "container", updated_at: stamp };
function attempt(id: string, agentId: string, phase: "solo" | "joint", start: number | null, finish: number): AOEAttempt {
  return { id, agent_id: agentId, phase, started_at: start, deadline: null, finished_at: finish, outcome: "solved" };
}
function question(id: string, attempts: AOEAttempt[]): AOEQuestion {
  return { question_id: id, parent_task_id: "batch", title: id, source_key: id, description: "q", status: "solved", difficulty: null, connection_info: "", submission_instructions: "", attempts, attempted_by: [], current_agents: [], attachments: [], flag: null, blackboard_seq: 0, report_state: "completed" };
}
const snapshot: TGA3RuntimeSnapshot = {
  task: { id: "batch", title: "batch", scene_id: "aoe", state: "running", created_at: stamp, updated_at: stamp, blackboard_seq: 0, event_seq: 0 },
  agents: [agent], blackboard: [], events: [], aoe: { task_id: "batch", phase: "solo", attempt_seconds: 1200, questions: [
    question("Solo win", [attempt("solo", agent.agent_id, "solo", 100, 165)]),
    question("Joint win", [attempt("joint-1", agent.agent_id, "joint", 200, 320), attempt("joint-2", "worker-claude", "joint", 200, 320)]),
    question("Peer win", [{ ...attempt("lost", agent.agent_id, "solo", 50, 80), outcome: "timeout" }, attempt("peer", "worker-claude", "solo", 100, 150)]),
    { ...question("Current question", []), status: "running", current_agents: [agent.agent_id] },
  ] },
};

describe("AOE Worker progress and record", () => {
  it("lists a successful reinforcement under solved with a reinforcement label and no credited time", async () => {
    const user = userEvent.setup();
    const helped = question("Assisted win", [
      { ...attempt("owner", "worker-claude", "solo", 100, 1000), success_credit: true },
      { ...attempt("helper", agent.agent_id, "solo", 700, 1000), phase: "reinforcement",
        first_attempt_id: "owner", success_credit: false },
    ]);
    helped.solver_agent_id = "worker-claude";
    const data = { ...snapshot, aoe: { ...snapshot.aoe!, questions: [helped] } };
    render(<MemoryRouter><TGA3AgentInspector snapshot={data} agent={agent} onChanged={() => {}} /></MemoryRouter>);
    const progress = screen.getByRole("heading", { name: "解题进度" }).closest("section")!;
    expect(progress).toHaveTextContent("已解出1 道");
    expect(progress).toHaveTextContent("未成功解出0 道");
    const averages = screen.getByRole("heading", { name: "各难度解题平均耗时" }).closest("section")!;
    expect(averages).toHaveTextContent("暂无耗时完整的已解出题目");
    await user.click(screen.getByRole("tab", { name: "战绩" }));
    const wins = screen.getByRole("region", { name: "已解出题目" });
    expect(wins).toHaveTextContent("Assisted win");
    expect(within(wins).getByText("增援", { exact: true })).toBeInTheDocument();
    expect(wins).not.toHaveTextContent("5分00秒");
    expect(screen.getByRole("region", { name: "未成功解出题目" })).not.toHaveTextContent("Assisted win");
  });
  it("shows the current question and solved count, then lists only this Worker's wins and actual attempt duration", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><TGA3AgentInspector snapshot={snapshot} agent={agent} onChanged={() => {}} /></MemoryRouter>);
    const progress = screen.getByRole("heading", { name: "解题进度" }).closest("section")!;
    expect(progress).toHaveTextContent("2 道"); expect(progress).toHaveTextContent("Current question");
    expect(screen.getAllByRole("tab").map(x => x.textContent)).toEqual(["概览", "活动", "模型与控制", "战绩"]);
    await user.click(screen.getByRole("tab", { name: "战绩" }));
    const record = screen.getByRole("region", { name: "OpenAI Worker 战绩" });
    const wins = within(record).getByRole("region", { name: "已解出题目" });
    const failed = within(record).getByRole("region", { name: "未成功解出题目" });
    expect(within(wins).getAllByRole("link").map(x => x.textContent)).toEqual(["Joint win", "Solo win"]);
    expect(record).toHaveTextContent("1分05秒"); expect(record).toHaveTextContent("2分00秒");
    expect(wins).toHaveTextContent("共同攻坚"); expect(wins).not.toHaveTextContent("Peer win");
    expect(failed).toHaveTextContent("Peer win"); expect(failed).not.toHaveTextContent("30秒");
    expect(failed.querySelector("strong")).toBeNull();
    expect(failed).toHaveTextContent("其他 Worker 已解出");
    expect(wins.compareDocumentPosition(failed) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(record).getByRole("link", { name: "Solo win" })).toHaveAttribute("href", "/tasks/batch/runtime?question=Solo%20win&tab=blackboard");
  });
  it("shows per-difficulty averages from cumulative wins and separates unsuccessful records below them", async () => {
    const user = userEvent.setup();
    const resumed = question("Resumed win", [
      { ...attempt("first", agent.agent_id, "solo", 0, 100), outcome: "timeout" },
      attempt("joint", agent.agent_id, "joint", 200, 400),
    ]);
    resumed.difficulty = "easy";
    const solo = question("Solo", [attempt("solo", agent.agent_id, "solo", 0, 100)]);
    solo.difficulty = "easy";
    const loss = { ...question("Failed", [{ ...attempt("loss", agent.agent_id, "solo", 0, 1000), outcome: "timeout" as const }]), status: "abandoned", difficulty: "easy" };
    const data = { ...snapshot, aoe: { ...snapshot.aoe!, questions: [resumed, solo, loss] } };
    render(<MemoryRouter><TGA3AgentInspector snapshot={data} agent={agent} onChanged={() => {}} /></MemoryRouter>);
    const averages = screen.getByRole("heading", { name: "各难度解题平均耗时" }).closest("section")!;
    expect(averages).toHaveTextContent("3分20秒 · 2 道"); // (300 + 100) / 2, not last-stage mean.
    await user.click(screen.getByRole("tab", { name: "战绩" }));
    const solved = screen.getByRole("region", { name: "已解出题目" });
    expect(within(solved).getByText("5分00秒", { exact: true })).toBeInTheDocument();
    expect(solved).not.toHaveTextContent("累计投入");
    expect(solved).not.toHaveTextContent("成功阶段");
    const failed = screen.getByRole("region", { name: "未成功解出题目" });
    expect(failed).toHaveTextContent("未成功解出 1 道题");
    expect(failed).not.toHaveTextContent("16分40秒");
    expect(failed).toHaveTextContent("已放弃");
  });
  it("does not invent elapsed time for missing starts or expose a Worker's record for Scout", async () => {
    const user = userEvent.setup();
    const data = { ...snapshot, aoe: { ...snapshot.aoe!, questions: [question("Legacy", [attempt("old", agent.agent_id, "solo", null, 100)])] } };
    const page = render(<MemoryRouter><TGA3AgentInspector snapshot={data} agent={agent} onChanged={() => {}} /></MemoryRouter>);
    await user.click(screen.getByRole("tab", { name: "战绩" })); expect(screen.getByText("未记录")).toBeInTheDocument();
    page.rerender(<MemoryRouter><TGA3AgentInspector snapshot={data} agent={{ ...agent, agent_id: "scout", role: "scout" }} onChanged={() => {}} /></MemoryRouter>);
    expect(screen.queryByRole("tab", { name: "战绩" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "true");
  });
});
