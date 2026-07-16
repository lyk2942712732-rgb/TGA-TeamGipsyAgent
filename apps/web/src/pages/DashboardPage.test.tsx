import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import type { TaskListItem } from "../api/tasks";

const task = (status = "completed"): TaskListItem => ({
  task_id: "task_history",
  name: "历史任务",
  mode: "ctf",
  target: "https://challenge.example",
  created_at: "2026-07-17T00:00:00Z",
  status,
  flags: 1,
  findings: 0,
  artifacts: 3,
});

describe("DashboardPage history deletion", () => {
  it("requires confirmation before deleting a historical task", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<DashboardPage tasks={[task()]} onNew={vi.fn()} onOpen={vi.fn()} onDelete={onDelete} />);

    fireEvent.click(screen.getByTitle("删除历史任务"));
    expect(screen.getByRole("dialog", { name: "删除历史任务？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith("task_history"));
  });

  it("does not allow a running task to be deleted", () => {
    render(<DashboardPage tasks={[task("running")]} onNew={vi.fn()} onOpen={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByTitle("运行中的任务需先取消")).toBeDisabled();
  });
});
