import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { normalizeRuntimeSnapshot } from "./models/normalize";

const useTaskRuntime = vi.fn();
vi.mock("./use-task-runtime", () => ({ useTaskRuntime: (...args: unknown[]) => useTaskRuntime(...args) }));
import { TaskRuntimePage } from "./TaskRuntimePage";

const snapshot = (solvers = 2) => normalizeRuntimeSnapshot({
  schema_version: 6,
  task: { id: "task", name: "Task workbench", mode: "ctf" },
  session: { status: "running", supervisor_solver_id: solvers ? "supervisor" : null, active_solver_count: solvers, max_active_workers: 2, task_budget_usage: { input_tokens: 12 }, stop_reason: null, timestamps: {}, turn_count: 1, max_turns: 20 },
  team: { task_id: "task", status: "running", supervisor_solver_id: solvers ? "supervisor" : null, max_active_workers: 2, max_total_solvers: 8, active_solver_count: solvers, solver_ids: solvers ? ["supervisor", "worker"] : [], version: 1, timestamps: {} },
  solvers: solvers ? [
    { task_id: "task", solver_id: "supervisor", definition_id: "supervisor", orchestration_role: "supervisor", specialties: ["planning"], parent_solver_id: null, assigned_intent_id: null, status: "running", current_summary: "coordinate", model_snapshot: {}, skill_snapshot: {}, capability_binding: {}, budget_usage: {}, timestamps: {} },
    ...(solvers > 1 ? [{ task_id: "task", solver_id: "worker", definition_id: "worker", orchestration_role: "worker", specialties: ["web"], parent_solver_id: "supervisor", assigned_intent_id: "intent", status: "running", current_summary: "inspect", model_snapshot: {}, skill_snapshot: {}, capability_binding: {}, budget_usage: {}, timestamps: {} }] : []),
  ] : [],
  intents: solvers > 1 ? [{ task_id: "task", intent_id: "intent", kind: "investigate", title: "Inspect", objective: "inspect", status: "running", assigned_solver_id: "worker", dependencies: [], priority: 1, budget: {}, created_at: "", updated_at: "" }] : [],
  worker_results: [], global_plan: null, knowledge: [], artifacts: [], evidence_claims: [], findings: [], actions: [], approvals: [], retrieval_runs: [], events: [], events_page: { after_seq: 0, next_after_seq: 0, has_more: false }, latest_seq: 0,
});

describe("TaskRuntimePage skeleton", () => {
  beforeEach(() => useTaskRuntime.mockReturnValue({ store: snapshot(), connection: "live", error: null, refresh: vi.fn() }));

  it("connects the task header, team tree, workspace and inspector to normalized state", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime?solver=worker&intent=intent&tab=timeline"]}><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Task workbench" })).toBeInTheDocument();
    expect(screen.getByRole("tree", { name: "Solver 团队" })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /worker/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "时间线" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("complementary", { name: "Solver 检查器" })).toHaveTextContent("inspect");
    expect(screen.getByRole("region", { name: "全局操作" })).toBeInTheDocument();
  });

  it("allows keyboard-native Solver selection and keeps state in the URL", () => {
    render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("treeitem", { name: /worker/ }));
    expect(screen.getByRole("treeitem", { name: /worker/ })).toHaveAttribute("aria-selected", "true");
  });

  it("switches the responsive team and inspector drawers without duplicating runtime state", () => {
    const { container } = render(<MemoryRouter initialEntries={["/tasks/task/runtime"]}><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);
    const teamDrawer = container.querySelector(".runtime-team-side");
    const inspectorDrawer = container.querySelector(".runtime-inspector-side");
    expect(teamDrawer).toHaveAttribute("data-open", "false");
    expect(inspectorDrawer).toHaveAttribute("data-open", "false");
    fireEvent.click(screen.getByRole("button", { name: "团队" }));
    expect(teamDrawer).toHaveAttribute("data-open", "true");
    fireEvent.click(screen.getByRole("button", { name: "检查器" }));
    expect(teamDrawer).toHaveAttribute("data-open", "false");
    expect(inspectorDrawer).toHaveAttribute("data-open", "true");
  });

  it("renders an explicit empty state without positional Solver assumptions", () => {
    useTaskRuntime.mockReturnValueOnce({ store: snapshot(0), connection: "live", error: null, refresh: vi.fn() });
    render(<MemoryRouter><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);
    expect(screen.getByText("尚无 Solver" )).toBeInTheDocument();
  });

  it("renders a single-Solver task through the same team projection", () => {
    useTaskRuntime.mockReturnValueOnce({ store: snapshot(1), connection: "live", error: null, refresh: vi.fn() });
    render(<MemoryRouter><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);
    expect(screen.getByRole("treeitem", { name: /supervisor/ })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Solver 检查器" })).toHaveTextContent("coordinate");
  });

  it("shows the exhausted model transport error and a recovery action", () => {
    const blocked = snapshot(1);
    blocked.session.status = "blocked";
    blocked.session.stopReason = "model_request_failed";
    blocked.team.status = "blocked";
    blocked.eventsBySeq[31] = {
      schemaVersion: 6, id: "event-31", taskId: "task", seq: 31,
      type: "AGENT_ERROR", solverId: "supervisor", intentId: null,
      payload: { phase: "model_turn", message: "provider request failed after 3 attempts", retryable: true, attempts: 3 },
      createdAt: "",
    };
    blocked.eventsBySeq[34] = {
      schemaVersion: 6, id: "event-34", taskId: "task", seq: 34,
      type: "SESSION_STOPPED", solverId: "supervisor", intentId: null,
      payload: { status: "blocked", reason: "model_request_failed", error: { code: "MODEL_REQUEST_FAILED", message: "provider request failed after 3 attempts", retryable: true, attempts: 3 } },
      createdAt: "",
    };
    blocked.latestSeq = 34;
    useTaskRuntime.mockReturnValueOnce({ store: blocked, connection: "live", error: null, refresh: vi.fn() });

    render(<MemoryRouter><TaskRuntimePage taskId="task" mode="runtime" /></MemoryRouter>);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("模型连接失败，任务已暂停");
    expect(alert).toHaveTextContent("provider request failed after 3 attempts");
    expect(alert).toHaveTextContent("已自动尝试 3 次");
    expect(screen.getByRole("button", { name: "重新连接并恢复" })).toBeEnabled();
  });
});
