import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TeamExplorer } from "../team/TeamExplorer";
import { IntentBoard } from "../intents/IntentBoard";
import { SolverInspector } from "./components/SolverInspector";
import { TaskCommandHeader } from "./components/TaskCommandHeader";
import { TaskOverview } from "./components/TaskOverview";
import { workbenchStore } from "./workbench-test-support";

describe("Phase 11 command workbench components", () => {
  it("summarizes task command state and exposes task-level controls", () => {
    const onControl = vi.fn();
    const onApprovals = vi.fn();
    render(<TaskCommandHeader store={workbenchStore()} connection="live" mode="runtime" onControl={onControl} onApprovals={onApprovals} />);
    expect(screen.getByRole("progressbar", { name: "总体进度" })).toHaveAttribute("value", "1");
    expect(screen.getByText("4 活动 / 1 完成 / 1 阻塞")).toBeInTheDocument();
    expect(screen.getByText("2", { selector: "dd" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "暂停全部" }));
    fireEvent.click(screen.getByRole("button", { name: "审批中心 (2)" }));
    expect(onControl).toHaveBeenCalledWith("pause");
    expect(onApprovals).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "报告" })).toHaveAttribute("target", "_blank");
  });

  it("shows two parallel workers with independent running and approval states", () => {
    const store = workbenchStore();
    render(<TeamExplorer store={store} selectedSolverId="worker-running" onSelect={() => undefined} />);
    const running = screen.getByRole("treeitem", { name: /worker-running/ });
    const approval = screen.getByRole("treeitem", { name: /worker-approval/ });
    expect(running).toHaveTextContent("运行中");
    expect(running).toHaveTextContent("intent-running");
    expect(running).toHaveTextContent("35 Token");
    expect(approval).toHaveTextContent("等待审批");
    expect(approval).toHaveTextContent("2 Skills");
    expect(screen.getByRole("treeitem", { name: /reviewer/ })).toHaveTextContent("evidence-review");
    expect(screen.getByRole("treeitem", { name: /reporter/ })).toHaveTextContent("已完成");
  });

  it("provides Kanban, bounded dependency graph and list views for Intent work items", () => {
    render(<IntentBoard store={workbenchStore()} selectedIntentId={null} onSelect={() => undefined} />);
    expect(screen.getByRole("region", { name: "Intent Kanban" })).toHaveTextContent("等待审批");
    fireEvent.click(screen.getByRole("button", { name: "依赖图" }));
    expect(screen.getByRole("figure", { name: "Intent 依赖图" })).toHaveTextContent("Resolve reviewer conflict");
    fireEvent.click(screen.getByRole("button", { name: "列表" }));
    expect(screen.getByRole("table", { name: "Intent 列表" })).toHaveTextContent("Write report");
  });

  it("answers command-level progress, verified knowledge, conflicts and completion criteria", () => {
    render(<TaskOverview store={workbenchStore()} onSelectSolver={() => undefined} onSelectIntent={() => undefined} />);
    expect(screen.getByRole("region", { name: "任务总体进度" })).toHaveTextContent("1 / 5 Intent 已完成");
    expect(screen.getByText("Endpoint returns version metadata")).toBeInTheDocument();
    expect(screen.getByText("Reviewer found conflicting version evidence")).toBeInTheDocument();
    expect(screen.getByText("confirmed evidence")).toBeInTheDocument();
  });

  it("drills into only the selected Solver and exposes inspector sections", () => {
    const store = workbenchStore();
    store.eventsBySeq[5].payload.hidden_thoughts = "top-secret-thought";
    store.eventsBySeq[5].payload.metadata = { chain_of_thought: "nested-secret-thought", safe: "persisted-metadata" };
    render(<SolverInspector store={store} solver={store.solversById.reviewer} />);
    const inspector = screen.getByRole("complementary", { name: "Solver 检查器" });
    expect(within(inspector).getByText("reviewer current activity")).toBeInTheDocument();
    fireEvent.click(within(inspector).getByRole("tab", { name: "Transcript" }));
    expect(inspector).toHaveTextContent("KNOWLEDGE_CONFLICT_DETECTED");
    expect(inspector).not.toHaveTextContent("Mapped HTTP surface");
    fireEvent.click(within(inspector).getByRole("button", { name: "协议模式" }));
    expect(inspector).toHaveTextContent("conflict_id");
    expect(inspector).toHaveTextContent("persisted-metadata");
    expect(inspector).not.toHaveTextContent("top-secret-thought");
    expect(inspector).not.toHaveTextContent("nested-secret-thought");
    fireEvent.click(within(inspector).getByRole("tab", { name: "Knowledge" }));
    expect(inspector).toHaveTextContent("Task Verified");
    expect(inspector).toHaveTextContent("Rejected / Superseded");
    fireEvent.click(within(inspector).getByRole("tab", { name: "Skills" }));
    expect(inspector).toHaveTextContent("evidence-method");
    expect(inspector).toHaveTextContent("task common guidance");
    fireEvent.click(within(inspector).getByRole("tab", { name: "Tools" }));
    expect(inspector).toHaveTextContent("http.request");
    expect(inspector).toHaveTextContent("调用 1 次");
  });
});
