import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DashboardResponse } from "../api/operations-query-adapter";
import { DashboardPage } from "./DashboardPage";

const value: DashboardResponse = {
  schema_version: 1,
  generated_at: "2026-07-30T00:00:00Z",
  metrics: { running_tasks: 2, pending_approvals: 1, awaiting_user_input: 1, blocked_tasks: 1, active_solvers: 3 },
  needs_attention: [{ id: "approval:action_one", kind: "approval", task_id: "task_one", task_name: "任务一", title: "等待审批", description: "filesystem.write", status: "pending", risk: "active", action_id: "action_one", updated_at: "2026-07-30T00:00:00Z" }],
  active_tasks: [{ task_id: "task_one", name: "任务一", mode: "ctf", status: "running", updated_at: "2026-07-30T00:00:00Z", active_solvers: 2, pending_approvals: 1, intent_total: 4, intent_completed: 2, findings: 1, artifacts: 2, turn_count: 3, max_turns: 20, needs_attention: true, latest_event: { seq: 8, type: "ACTION_PROPOSED", created_at: "2026-07-30T00:00:00Z" } }],
  recent_completed: [{ task_id: "task_done", name: "已完成任务", mode: "ctf", status: "completed", updated_at: "2026-07-29T00:00:00Z", active_solvers: 0, pending_approvals: 0, intent_total: 2, intent_completed: 2, findings: 2, artifacts: 3, turn_count: 6, max_turns: 20, needs_attention: false, latest_event: null }],
  system_status: [{ id: "api", label: "API", status: "healthy", detail: "聚合查询可用", available: true }, { id: "scheduler", label: "Scheduler", status: "unavailable", detail: "暂不可探测", available: false }],
  unavailable_metrics: [],
};

function renderPage(overrides: Partial<DashboardResponse> = {}) {
  const callbacks = { onNew: vi.fn(), onTask: vi.fn(), onRuntime: vi.fn(), onApprovals: vi.fn() };
  const result = render(<DashboardPage value={{ ...value, ...overrides }} {...callbacks} />);
  return { callbacks, ...result };
}

describe("DashboardPage", () => {
  it("renders real operational metrics, attention, work, outcomes, and system signals", () => {
    const { container } = renderPage();
    expect(container.querySelector(".operations-metrics")).toHaveTextContent("运行中任务2");
    expect(container.querySelector(".operations-metrics")).toHaveTextContent("等待审批1");
    expect(screen.getByRole("heading", { name: "需要你的处理" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "任务一" })).toBeInTheDocument();
    expect(screen.getByText("已完成任务")).toBeInTheDocument();
    expect(screen.getByText("暂不可探测")).toBeInTheDocument();
  });

  it("opens the filtered approval center and task Runtime from summaries", () => {
    const { callbacks } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: /审批.*等待审批.*任务一/ }));
    expect(callbacks.onApprovals).toHaveBeenCalledWith("task_one");
    fireEvent.click(screen.getByRole("button", { name: "进入运行" }));
    expect(callbacks.onRuntime).toHaveBeenCalledWith("task_one");
  });

  it("shows honest empty states without inventing records", () => {
    renderPage({ needs_attention: [], active_tasks: [], recent_completed: [] });
    expect(screen.getByText("当前没有需要人工处理的任务。")).toBeInTheDocument();
    expect(screen.getByText("当前没有活动任务。")).toBeInTheDocument();
    expect(screen.getByText("尚无最近完成任务或确认结果。")).toBeInTheDocument();
  });
});
