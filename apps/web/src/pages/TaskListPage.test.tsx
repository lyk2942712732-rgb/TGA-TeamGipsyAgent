import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchTaskList: vi.fn() }));
vi.mock("../api/task-query-adapter", async (original) => ({ ...await original<typeof import("../api/task-query-adapter")>(), ...mocks }));

import { TaskListPage } from "./TaskListPage";

const rows = [{ task_id: "task_one", name: "Alpha task", mode: "ctf", status: "awaiting_approval", created_at: "2026-07-30T00:00:00Z", flags: 0, findings: 1, artifacts: 2, active_solvers: 1, pending_approvals: 1, needs_attention: true, intent_total: 3, intent_completed: 1 }];

function renderPage(entry = "/tasks") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><TaskListPage /><Location /></MemoryRouter></QueryClientProvider>);
}
function Location() { const location = useLocation(); return <output data-testid="location">{location.search}</output>; }

describe("TaskListPage", () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.fetchTaskList.mockResolvedValue({ tasks: rows, total: 1, offset: 0, limit: 100, next_offset: null }); });

  it("loads the list once and never requests Runtime snapshots", async () => {
    renderPage();
    expect(await screen.findByRole("table", { name: "任务列表" })).toBeInTheDocument();
    expect(mocks.fetchTaskList).toHaveBeenCalledTimes(1);
    expect(mocks.fetchTaskList).toHaveBeenCalledWith(expect.objectContaining({ limit: 100 }));
  });

  it("restores real filters from the URL and writes view mode back", async () => {
    const user = userEvent.setup();
    renderPage("/tasks?query=Alpha&mode=ctf&status=awaiting_approval&needs_attention=true");
    await waitFor(() => expect(mocks.fetchTaskList).toHaveBeenCalledWith(expect.objectContaining({ query: "Alpha", mode: "ctf", status: "awaiting_approval", needsAttention: true })));
    expect(screen.getByLabelText("搜索任务")).toHaveValue("Alpha");
    await user.click(screen.getByRole("button", { name: "卡片视图" }));
    expect(screen.getByTestId("location")).toHaveTextContent("view=cards");
    expect(await screen.findByRole("heading", { name: "Alpha task" })).toBeInTheDocument();
  });

  it("keeps real tasks ahead of the reference sample rows", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: "任务列表" });
    const names = [...table.querySelectorAll("tbody .task-name-cell strong")].map((node) => node.textContent);
    expect(names[0]).toBe("Alpha task");
    expect(names).toContain("Web API 安全测试");
  });

  it("does not navigate to a sample task that was never created", async () => {
    const user = userEvent.setup();
    mocks.fetchTaskList.mockResolvedValue({ tasks: [], total: 0 });
    renderPage();
    const table = await screen.findByRole("table", { name: "任务列表" });
    await user.click(within(table).getByText("Web API 安全测试"));
    expect(screen.getByTestId("location")).not.toHaveTextContent("sample-");
  });
});
