import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionRuntimePage, redact } from "./SessionRuntimePage";
import type { RuntimeSnapshot } from "../runtime/event-types";

const sessionRuntime = vi.fn();
const control = vi.fn();
vi.mock("../runtime/session-store", () => ({ useSessionRuntime: (...args: unknown[]) => sessionRuntime(...args) }));
vi.mock("../runtime/api-v2", () => ({ runtimeApi: { control: (...args: unknown[]) => control(...args), hint: vi.fn(), reportUrl: () => "/report", artifactUrl: () => "/artifact" } }));
vi.mock("../components/runtime/ResizableWorkspace", () => ({ ResizableWorkspace: ({ board, timeline, evidence }: { board: React.ReactNode; timeline: React.ReactNode; evidence: React.ReactNode }) => <>{board}{timeline}{evidence}</> }));

function fixture(): RuntimeSnapshot {
  return { task: { id: "task_1", name: "Web CTF", mode: "ctf", target: "http://target", scope: ["target"] }, session: { status: "running", turn_count: 1, max_turns: 48 }, solvers: [], challenge: { status: "active", status_reason: "" }, subagents: [], board: { hypotheses: [{ id: "hyp_1", statement: "入口可能可达", attack_class: "recon", entry_point: "/", rationale: "观察到入口", next_test: "请求首页", status: "rejected", confidence: 0.4, attempt_count: 1, evidence_artifact_ids: [], last_result: "首页不包含交互入口" }], memory: [] }, actions: [{ id: "act_1", capability: "http.request", target: "http://target", status: "succeeded", rationale: "验证入口", summary: "真实执行结果", artifact_ids: [] }], flags: [], findings: [], artifacts: [], events: [{ id: "evt_1", task_id: "task_1", seq: 1, type: "ACTION_PROPOSED", payload: { action_id: "act_1", capability: "http.request", target: "http://target" }, created_at: "2026-07-13T00:00:00Z" }, { id: "evt_2", task_id: "task_1", seq: 2, type: "ACTION_FINISHED", payload: { action_id: "act_1", status: "succeeded", summary: "真实执行结果" }, created_at: "2026-07-13T00:00:01Z" }, { id: "evt_3", task_id: "task_1", seq: 3, type: "FLAG_CONFIRMED", payload: { value: "flag{unproven}" }, created_at: "2026-07-13T00:00:02Z" }], latest_seq: 3 };
}

describe("SessionRuntimePage", () => {
  beforeEach(() => { control.mockReset(); sessionRuntime.mockReturnValue({ snapshot: fixture(), connection: "live", error: null, refresh: vi.fn() }); });
  it("keeps a proposed action visibly separate from the finished result", () => { render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); expect(screen.getByText("已计划，未执行", { exact: false })).toBeInTheDocument(); expect(screen.getByText("真实执行结果")).toBeInTheDocument(); });
  it("shows legacy rejected ideas without treating an artifact-less legacy flag as a Session result", () => { render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); expect(screen.getByTestId("flow-hypothesis").getAttribute("aria-label")).toContain("失败边界: 首页不包含交互入口"); expect(screen.getByText(/确认缺少证据/)).toBeInTheDocument(); fireEvent.click(screen.getByRole("button", { name: /Evidence 0/ })); expect(screen.getByText("No final result yet.")).toBeInTheDocument(); expect(screen.queryByText("Solver result")).toBeNull(); });
  it("restores controls and exposes the server error when cancellation is rejected", async () => { control.mockRejectedValueOnce(new Error("manager rejected cancellation")); render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />); fireEvent.click(screen.getByRole("button", { name: "取消" })); fireEvent.click(screen.getByRole("button", { name: "确认取消" })); await waitFor(() => expect(screen.getByText("manager rejected cancellation")).toBeInTheDocument()); expect(screen.getByRole("button", { name: "取消" })).toBeEnabled(); });
  it("redacts sensitive artifact preview values", () => { const preview = redact("Authorization: Bearer abc token=xyz api_key=qwe"); expect(preview).not.toContain("abc"); expect(preview).not.toContain("xyz"); expect(preview).not.toContain("qwe"); });
  it("shows solver state and only promotes an evidence-backed flag", () => {
    const rich = fixture();
    rich.session.active_solver_id = "solver_1";
    rich.solvers = [{ id: "solver_1", role: "recon", status: "running", model_name: "model-a" }];
    rich.subagents = [{ request: { id: "subreq_1", parent_solver_id: "solver_1", role: "recon", objective: "梳理已授权路由", hypothesis_ids: ["hyp_1"], max_actions: 8 }, solver_id: "solver_1", status: "completed", output: { status: "completed", artifact_ids: [], coverage_gaps: ["认证后路由"], next_recommendation: "转交定向验证" } }];
    rich.flags = [{ value: "flag{evidence_backed}", evidence_artifact_id: "artifact_1" }];
    rich.artifacts = [{ id: "artifact_1", kind: "http_response", path: "landing.txt" }];
    rich.events = [...rich.events, { id: "evt_4", task_id: "task_1", seq: 4, type: "SKILLS_LOADED", payload: { skills: [{ name: "web-recon" }] }, created_at: "2026-07-13T00:00:03Z" }, { id: "evt_5", task_id: "task_1", seq: 5, type: "ACTION_APPROVED", payload: { action_id: "act_1" }, created_at: "2026-07-13T00:00:04Z" }, { id: "evt_6", task_id: "task_1", seq: 6, type: "RESULT_REJECTED", payload: { reason: "unpersisted_artifact_reference" }, created_at: "2026-07-13T00:00:05Z" }];
    sessionRuntime.mockReturnValue({ snapshot: rich, connection: "live", error: null, refresh: vi.fn() });
    render(<SessionRuntimePage taskId="task_1" mode="runtime" onReplay={vi.fn()} />);
    expect(screen.getByTestId("solver-lane")).toHaveTextContent("Recon");
    expect(screen.getByTestId("challenge-status")).toHaveTextContent("active");
    fireEvent.click(screen.getByTitle("solver_1"));
    expect(screen.getByText(/认证后路由/)).toBeInTheDocument();
    expect(screen.getByText(/转交定向验证/)).toBeInTheDocument();
    expect(screen.getByTestId("flag-hero")).toHaveTextContent("flag{evidence_backed}");
    expect(screen.getByText(/为本回合加载技能/)).toBeInTheDocument();
    expect(screen.getByText(/已通过策略批准/)).toBeInTheDocument();
    expect(screen.getByText(/执行结果未通过校验/)).toBeInTheDocument();
  });
});
