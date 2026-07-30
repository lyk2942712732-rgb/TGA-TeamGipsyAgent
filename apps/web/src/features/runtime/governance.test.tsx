import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalCenter } from "../approvals/ApprovalCenter";
import { InterventionDialog } from "./components/InterventionDialog";
import { workbenchStore } from "./workbench-test-support";

const api = vi.hoisted(() => ({ approvalDecision: vi.fn(), intervention: vi.fn() }));
vi.mock("../../runtime/api-v2", () => ({ runtimeApi: api }));

describe("Phase 11 governance UI", () => {
  beforeEach(() => { vi.clearAllMocks(); api.approvalDecision.mockResolvedValue({ accepted: true }); api.intervention.mockResolvedValue({ accepted: true }); });

  it("renders a global approval queue and scopes each one-time decision", async () => {
    render(<ApprovalCenter store={workbenchStore()} readonly={false} onChanged={() => undefined} />);
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(screen.getByText("Persistent shared write")).toBeInTheDocument();
    expect(screen.getByText("Preview only")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准 action-write" }));
    await waitFor(() => expect(api.approvalDecision).toHaveBeenCalledWith("task", "action-write", "approve"));
    fireEvent.click(screen.getByRole("button", { name: "拒绝 action-http" }));
    await waitFor(() => expect(api.approvalDecision).toHaveBeenCalledWith("task", "action-http", "reject"));
    expect(screen.queryByText(/永久自动批准/)).toBeNull();
  });

  it("submits a targeted intervention and explains its authority boundary", async () => {
    render(<InterventionDialog store={workbenchStore()} open onClose={() => undefined} onSubmitted={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "补充任务信息" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("作用域"), { target: { value: "solver" } });
    fireEvent.change(screen.getByLabelText("目标 Solver"), { target: { value: "worker-running" } });
    fireEvent.change(screen.getByLabelText("类型"), { target: { value: "constraint" } });
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "Do not modify the target" } });
    expect(screen.getByText(/不会扩大 ExecutionPolicy/)).toBeInTheDocument();
    expect(screen.getByText(/不会默认广播给所有 Solver/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "提交 Intervention" }));
    await waitFor(() => expect(api.intervention).toHaveBeenCalledWith("task", { kind: "constraint", content: "Do not modify the target", scope: "solver", target_id: "worker-running" }));
  });
});
