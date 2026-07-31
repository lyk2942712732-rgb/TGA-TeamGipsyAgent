import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchTaskDetail: vi.fn(), fetchTaskTeam: vi.fn(), fetchTaskInputs: vi.fn(), fetchTaskEvidence: vi.fn(), fetchTaskHistory: vi.fn() }));
vi.mock("../api/task-query-adapter", async (original) => ({ ...await original<typeof import("../api/task-query-adapter")>(), ...mocks }));

import { TaskDetailPage } from "./TaskDetailPage";

const detail = {
  schema_version: 6, task_id: "task_one",
  task: { id: "task_one", name: "Alpha task", mode: "ctf", goal: "Recover evidence", schema_version: 6 },
  task_spec: { task_id: "task_one", objective: "Recover evidence", instructions: [{ id: "one", content: "Inspect input" }], constraints: [], success_criteria: [], resources: [] },
  lifecycle: { created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:01:00Z", status: "running", turn_count: 2, max_turns: 20, active_solvers: 1, pending_approvals: 0, intent_total: 2, intent_completed: 1, flags: 0, findings: 1, artifacts: 2, needs_attention: false, latest_event: { seq: 4, type: "SOLVER_STARTED", created_at: "2026-07-30T00:01:00Z" } },
  input_summary: { prompt_present: true, prompt_preview: "Inspect input", file_count: 1, files: [], task_entry_url: null },
  config_snapshot: { mode_config: { mode: "ctf" }, execution_policy: { preset: "autonomous_ctf", network: { access: "public_internet" }, high_impact: { mode: "approval_required" } }, execution_budget: {}, model: null, mcp_capabilities: {}, task_common_skills: null, agent_prompt: null },
};

function renderPage() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><MemoryRouter><TaskDetailPage taskId="task_one" /></MemoryRouter></QueryClientProvider>); }

describe("TaskDetailPage lazy queries", () => {
  beforeEach(() => {
    vi.clearAllMocks(); mocks.fetchTaskDetail.mockResolvedValue(detail);
    mocks.fetchTaskTeam.mockResolvedValue({ task_id: "task_one", team: { status: "running", active_solver_count: 1 }, solvers: [] });
    mocks.fetchTaskInputs.mockResolvedValue({ task_goal: "Recover evidence", prompt: "Inspect input", files: [] });
    mocks.fetchTaskEvidence.mockResolvedValue({ task_id: "task_one", artifacts: { items: [], total: 0 }, evidence_claims: { items: [], total: 0 }, findings: { items: [], total: 0 } });
    mocks.fetchTaskHistory.mockResolvedValue({ events: [], latest_seq: 0, has_more: false });
  });

  it("loads only what 概览 renders on first open", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Alpha task" })).toBeInTheDocument();
    expect(mocks.fetchTaskDetail).toHaveBeenCalledTimes(1);
    // 概览 shows 关键发现 and 最近事件, so those two projections load with it.
    await waitFor(() => expect(mocks.fetchTaskEvidence).toHaveBeenCalledTimes(1));
    expect(mocks.fetchTaskHistory).toHaveBeenCalledTimes(1);
    // Team and inputs are not on 概览 and must stay lazy.
    expect(mocks.fetchTaskTeam).not.toHaveBeenCalled();
    expect(mocks.fetchTaskInputs).not.toHaveBeenCalled();
  });

  it("loads team and input projections only when their tab opens", async () => {
    const user = userEvent.setup(); renderPage(); await screen.findByRole("heading", { name: "Alpha task" });
    await user.click(screen.getByRole("tab", { name: /团队/ })); await waitFor(() => expect(mocks.fetchTaskTeam).toHaveBeenCalledTimes(1));
    expect(mocks.fetchTaskInputs).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: /输入/ })); await waitFor(() => expect(mocks.fetchTaskInputs).toHaveBeenCalledTimes(1));
  });

  it("shows elapsed run time and intent progress from the lifecycle projection", async () => {
    const { container } = renderPage();
    await screen.findByRole("heading", { name: "Alpha task" });
    const cards = container.querySelectorAll(".task-stat-card");
    expect(cards).toHaveLength(5);
    expect(cards[0]).toHaveTextContent("已运行 1 分钟");
    expect(cards[1]).toHaveTextContent("1 / 2 步骤已完成");
  });
});
