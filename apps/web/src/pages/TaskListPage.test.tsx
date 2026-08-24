import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listTasks: vi.fn() }));
vi.mock("../api/tga3-tasks", () => ({ listTasks: mocks.listTasks }));
import { TaskListPage } from "./TaskListPage";

function renderPage(entry = "/tasks") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><TaskListPage /></MemoryRouter></QueryClientProvider>);
}

describe("TaskListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTasks.mockResolvedValue([{ id: "task-one", title: "Alpha task", scene_id: "pwn", state: "running", blackboard_seq: 3, dialogue_seq: 5, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z" }]);
  });

  it("lists native TGA3 fields", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: "任务列表" });
    expect(table).toHaveTextContent("Alpha task");
    expect(table).toHaveTextContent("task-one");
    expect(table).toHaveTextContent("3");
    expect(table).toHaveTextContent("5");
    expect(mocks.listTasks).toHaveBeenCalledTimes(1);
  });

  it("filters native scene and state query parameters", async () => {
    renderPage("/tasks?scene=forensics&state=completed");
    expect(await screen.findByText("没有匹配的任务")).toBeInTheDocument();
  });
});
