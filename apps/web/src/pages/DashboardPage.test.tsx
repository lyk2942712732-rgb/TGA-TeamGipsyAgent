import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DashboardResponse } from "../api/operations-query-adapter";
import { DashboardPage } from "./DashboardPage";

const value: DashboardResponse = {
  schema_version: 1,
  generated_at: "2026-07-30T00:00:00Z",
  metrics: { running_tasks: 2, pending_approvals: 1, awaiting_user_input: 1, blocked_tasks: 1, active_solvers: 3 },
  needs_attention: [{ id: "approval:action_one", kind: "approval", task_id: "task_one", task_name: "任务一", title: "等待审批", description: "filesystem.write", status: "pending", risk: "active", action_id: "action_one", updated_at: "2026-07-30T00:00:00Z" }],
  active_tasks: [{ task_id: "task_one", name: "任务一", mode: "ctf", status: "blocked", updated_at: "2026-07-30T00:00:00Z", active_solvers: 2, pending_approvals: 1, intent_total: 4, intent_completed: 2, findings: 1, artifacts: 2, turn_count: 3, max_turns: 20, needs_attention: true, latest_event: { seq: 8, type: "ACTION_PROPOSED", created_at: "2026-07-30T00:00:00Z" } }],
  recent_completed: [{ task_id: "task_done", name: "已完成任务", mode: "ctf", status: "completed", updated_at: "2026-07-29T00:00:00Z", active_solvers: 0, pending_approvals: 0, intent_total: 2, intent_completed: 2, findings: 2, artifacts: 3, turn_count: 6, max_turns: 20, needs_attention: false, latest_event: null }],
  system_status: [{ id: "api", label: "API", status: "healthy", detail: "聚合查询可用", available: true }, { id: "sqlite", label: "SQLite", status: "healthy", detail: "只读检查通过", available: true }, { id: "scheduler", label: "Scheduler", status: "unavailable", detail: "暂不可探测", available: false }],
  unavailable_metrics: [],
};

function renderPage(overrides: Partial<DashboardResponse> = {}) {
  const callbacks = {
    onNew: vi.fn(), onTask: vi.fn(), onTasks: vi.fn(), onRuntime: vi.fn(),
    onApprovals: vi.fn(), onSystem: vi.fn(), onReports: vi.fn(),
  };
  const result = render(<DashboardPage value={{ ...value, ...overrides }} {...callbacks} />);
  return { callbacks, ...result };
}

describe("DashboardPage", () => {
  it("adds the sample offset to the real API counts so the cards agree with the metrics", () => {
    const { container } = renderPage();
    const cards = container.querySelectorAll(".dashboard-metric");
    expect(cards).toHaveLength(6);
    expect(cards[0]).toHaveTextContent("运行中任务");
    expect(cards[0]).toHaveTextContent("6"); // 2 real + 4 sample
    expect(cards[5]).toHaveTextContent("活动 Solver");
    expect(cards[5]).toHaveTextContent("10"); // 3 real + 7 sample
  });

  it("pads each list to the reference length with the real records first", () => {
    const { container } = renderPage();
    const [attention, activeWork] = container.querySelectorAll(".ref-card");

    const attentionRows = within(attention as HTMLElement).getAllByRole("listitem");
    expect(attentionRows).toHaveLength(5);
    expect(attentionRows[0]).toHaveTextContent("等待审批");

    const taskRows = within(activeWork as HTMLElement).getAllByRole("listitem");
    expect(taskRows).toHaveLength(4);
    expect(taskRows[0]).toHaveTextContent("任务一");
  });

  it("labels an active task by its real status instead of assuming it is running", () => {
    const { container } = renderPage();
    const taskRows = within(container.querySelectorAll(".ref-card")[1] as HTMLElement).getAllByRole("listitem");
    expect(taskRows[0]).toHaveTextContent("已阻塞");
    expect(taskRows[1]).toHaveTextContent("运行中");
  });

  it("renders the reference's five system components", () => {
    const { container } = renderPage();
    const labels = [...container.querySelectorAll(".system-rows strong")].map((node) => node.textContent);
    expect(labels).toEqual(["Model Providers", "MCP Servers", "Scheduler", "Execution Runtime", "Database"]);
  });

  it("carries no sample or unimplemented marker into the page", () => {
    const { container } = renderPage({ needs_attention: [], active_tasks: [], recent_completed: [] });
    expect(container.querySelector(".sample-banner")).toBeNull();
    expect(container.querySelector(".sample-badge")).toBeNull();
    expect(container.textContent).not.toContain("项目没有实现");
  });

  it("routes sample rows to the section index so no fabricated task id reaches the router", () => {
    const { callbacks, container } = renderPage({ needs_attention: [], active_tasks: [], recent_completed: [] });
    const [attention, activeWork] = container.querySelectorAll(".ref-card");

    fireEvent.click(within(attention as HTMLElement).getByRole("button", { name: "回答问题" }));
    expect(callbacks.onTasks).toHaveBeenCalled();
    expect(callbacks.onTask).not.toHaveBeenCalled();

    fireEvent.click(within(attention as HTMLElement).getByRole("button", { name: "查看并审批" }));
    expect(callbacks.onApprovals).toHaveBeenCalledWith();

    fireEvent.click(within(activeWork as HTMLElement).getAllByRole("button")[0]);
    expect(callbacks.onRuntime).not.toHaveBeenCalled();
  });

  it("opens the approval center and task runtime from real summaries", () => {
    const { callbacks, container } = renderPage();
    const [attention, activeWork] = container.querySelectorAll(".ref-card");

    const realAttention = within(attention as HTMLElement).getAllByRole("listitem")[0];
    fireEvent.click(within(realAttention).getByRole("button", { name: "查看并审批" }));
    expect(callbacks.onApprovals).toHaveBeenCalledWith("task_one");

    fireEvent.click(within(activeWork as HTMLElement).getByRole("button", { name: /任务一/ }));
    expect(callbacks.onRuntime).toHaveBeenCalledWith("task_one");
  });
});
