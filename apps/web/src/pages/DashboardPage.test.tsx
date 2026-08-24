import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AttentionItem } from "../api/tga3-attention";
import type { SystemHealth } from "../api/tga3-system";
import type { TGA3TaskListItem } from "../api/tga3-tasks";
import { DashboardPage } from "./DashboardPage";

const tasks: TGA3TaskListItem[] = [
  { id: "active", title: "Active task", scene_id: "pwn", state: "running", blackboard_seq: 4, dialogue_seq: 7, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:01:00Z" },
  { id: "done", title: "Completed task", scene_id: "forensics", state: "completed", blackboard_seq: 10, dialogue_seq: 9, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:02:00Z" },
];
const attention: AttentionItem[] = [{ id: "q1", kind: "question", task_id: "active", task_title: "Active task", task_state: "waiting_user", agent_id: "worker-openai", title: "Need input", detail: "Provide scope", question_id: "question-1", created_at: "2026-01-01T00:01:00Z" }];
const health: SystemHealth = { components: [{ id: "runtime", label: "TGA3 Control Plane", status: "healthy", detail: "tga3", latencyMs: 2 }] };

describe("DashboardPage", () => {
  it("renders native TGA3 task, attention, and health data", () => {
    render(<DashboardPage tasks={tasks} attention={attention} health={health} onNew={vi.fn()} onTask={vi.fn()} onTasks={vi.fn()} onApprovals={vi.fn()} onSystem={vi.fn()} onReports={vi.fn()} />);
    expect(screen.getByText("Active task")).toBeInTheDocument();
    expect(screen.getByText("Need input")).toBeInTheDocument();
    expect(screen.getByText("TGA3 Control Plane")).toBeInTheDocument();
    expect(screen.getByText("Completed task")).toBeInTheDocument();
  });

  it("opens a task by its TGA3 id", () => {
    const onTask = vi.fn();
    render(<DashboardPage tasks={tasks} attention={[]} health={health} onNew={vi.fn()} onTask={onTask} onTasks={vi.fn()} onApprovals={vi.fn()} onSystem={vi.fn()} onReports={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Active task/ }));
    expect(onTask).toHaveBeenCalledWith("active");
  });
});
