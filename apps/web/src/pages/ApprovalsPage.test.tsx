import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GlobalApproval } from "../api/operations-query-adapter";

const mocks = vi.hoisted(() => ({ fetchGlobalApprovals: vi.fn(), decideGlobalApproval: vi.fn() }));
vi.mock("../api/operations-query-adapter", async (original) => ({
  ...await original<typeof import("../api/operations-query-adapter")>(),
  ...mocks,
}));

import { ApprovalsPage } from "./ApprovalsPage";

const approval: GlobalApproval = {
  approval_id: "approval_one",
  task_id: "task_one",
  task_name: "任务一",
  solver_id: "solver_one",
  intent_id: "intent_one",
  action_id: "action_one",
  action_kind: "tool",
  capability: "filesystem.write",
  target: "workspace/report.txt",
  risk: "active",
  effect: { description: "写入报告" },
  rationale: "保存分析结果",
  expected_outcome: "生成报告",
  alternative_analysis: "可以仅在内存中保留，但结果无法交付",
  alternatives: ["不执行"],
  reversibility: "可删除文件",
  expires_at: "2099-07-30T01:00:00Z",
  status: "pending",
  decision_allowed: true,
  decision_block_reason: null,
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

function renderPage(entry = "/approvals") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><ApprovalsPage /><LocationProbe /></MemoryRouter></QueryClientProvider>);
}

describe("ApprovalsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchGlobalApprovals.mockResolvedValue({ schema_version: 1, offset: 0, limit: 12, total: 1, next_offset: null, items: [approval], filters: {} });
    mocks.decideGlobalApproval.mockResolvedValue({ accepted: true, status: "approved" });
  });

  it("restores filters from the URL and persists status, filters, and page", async () => {
    const user = userEvent.setup();
    renderPage("/approvals?status=rejected&task_id=task_old&page=2");
    await waitFor(() => expect(mocks.fetchGlobalApprovals).toHaveBeenCalledWith(expect.objectContaining({ status: "rejected", taskId: "task_old", page: 2, limit: 12 })));
    expect(screen.getByRole("tab", { name: "已拒绝" })).toHaveAttribute("aria-selected", "true");
    // The reference filters are dropdowns populated from the loaded queue.
    await user.selectOptions(await screen.findByLabelText("任务筛选"), "task_one");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("task_id=task_one"));
    expect(screen.getByTestId("location")).toHaveTextContent("status=rejected");
    expect(screen.getByTestId("location")).not.toHaveTextContent("page=2");
  });

  it("approves once through the existing task-level decision adapter", async () => {
    const user = userEvent.setup();
    renderPage();
    const record = (await screen.findByRole("heading", { name: "filesystem.write" })).closest("article");
    await user.click(within(record as HTMLElement).getByRole("button", { name: "批准一次" }));
    const dialog = screen.getByRole("dialog", { name: "批准本次操作？" });
    await user.click(within(dialog).getByRole("button", { name: "批准一次" }));
    await waitFor(() => expect(mocks.decideGlobalApproval).toHaveBeenCalledWith(approval, "approve"));
    expect(await screen.findByText("已提交一次性批准")).toBeInTheDocument();
  });

  it("rejects through the same task-level adapter", async () => {
    const user = userEvent.setup();
    renderPage();
    const record = (await screen.findByRole("heading", { name: "filesystem.write" })).closest("article");
    await user.click(within(record as HTMLElement).getByRole("button", { name: "拒绝" }));
    const dialog = screen.getByRole("dialog", { name: "拒绝该操作？" });
    await user.click(within(dialog).getByRole("button", { name: "确认拒绝" }));
    await waitFor(() => expect(mocks.decideGlobalApproval).toHaveBeenCalledWith(approval, "reject"));
  });

  it("does not allow an elapsed pending approval to be decided", async () => {
    mocks.fetchGlobalApprovals.mockResolvedValue({ schema_version: 1, offset: 0, limit: 12, total: 1, next_offset: null, items: [{ ...approval, decision_allowed: false, decision_block_reason: "审批已过期" }], filters: {} });
    renderPage();
    const record = (await screen.findByRole("heading", { name: "filesystem.write" })).closest("article");
    expect(within(record as HTMLElement).getByRole("button", { name: "批准一次" })).toBeDisabled();
    expect(within(record as HTMLElement).getByRole("button", { name: "拒绝" })).toBeDisabled();
    expect(mocks.decideGlobalApproval).not.toHaveBeenCalled();
  });

  it("shows an empty queue rather than fabricated approvals", async () => {
    mocks.fetchGlobalApprovals.mockResolvedValue({ schema_version: 1, offset: 0, limit: 12, total: 0, next_offset: null, items: [], filters: {} });
    renderPage();
    expect(await screen.findByText("当前筛选下没有待处理审批。")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("surfaces a queue error instead of masking it with reference data", async () => {
    mocks.fetchGlobalApprovals.mockRejectedValue(new Error("approval service offline"));
    renderPage();
    expect(await screen.findByText("approval service offline")).toBeInTheDocument();
    expect(screen.queryByText("审批服务器文件")).not.toBeInTheDocument();
  });
});
