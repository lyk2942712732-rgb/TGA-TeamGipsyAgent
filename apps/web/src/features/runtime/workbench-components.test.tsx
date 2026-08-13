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

    // Reference 05 collapses the task controls into the 任务操作 menu.
    expect(screen.queryByRole("button", { name: "暂停全部" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /任务操作/ }));
    expect(screen.queryByRole("menuitem", { name: "暂停全部" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "审批中心 (2)" }));
    expect(onControl).not.toHaveBeenCalled();
    expect(onApprovals).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: /任务操作/ }));
    expect(screen.getByRole("menuitem", { name: "报告" })).toHaveAttribute("target", "_blank");
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
    expect(approval).toHaveTextContent("0 Skill docs");
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

  it("answers command-level progress, confirmed findings, blockers and completion criteria", () => {
    const { container } = render(<TaskOverview store={workbenchStore()} onSelectSolver={() => undefined} onSelectIntent={() => undefined} />);
    expect(screen.getByRole("region", { name: "任务总体进度" })).toHaveTextContent("1 / 5 Intent 已完成");
    expect(screen.getByText("Version exposed")).toBeInTheDocument();
    expect(container.querySelector(".overview-risks")).toHaveTextContent("worker-approval：awaiting_approval");
    expect(screen.getByText("confirmed evidence")).toBeInTheDocument();
  });

  it("drills into only the selected Solver and exposes inspector sections", () => {
    const store = workbenchStore();
    store.eventsBySeq[5].payload.hidden_thoughts = "top-secret-thought";
    store.eventsBySeq[5].payload.metadata = { chain_of_thought: "nested-secret-thought", safe: "persisted-metadata" };
    render(<SolverInspector store={store} solver={store.solversById.reviewer} />);
    const inspector = screen.getByRole("complementary", { name: "Solver 检查器" });
    expect(within(inspector).getByText("reviewer current activity")).toBeInTheDocument();
    fireEvent.click(within(inspector).getByRole("tab", { name: "事件日志" }));
    expect(inspector).toHaveTextContent("KNOWLEDGE_CONFLICT_DETECTED");
    expect(inspector).not.toHaveTextContent("Mapped HTTP surface");
    fireEvent.click(within(inspector).getByRole("button", { name: "协议模式" }));
    expect(inspector).toHaveTextContent("conflict_id");
    expect(inspector).toHaveTextContent("persisted-metadata");
    expect(inspector).not.toHaveTextContent("top-secret-thought");
    expect(inspector).not.toHaveTextContent("nested-secret-thought");
    fireEvent.click(within(inspector).getByRole("tab", { name: "Skills" }));
    expect(inspector).toHaveTextContent("evidence-method");
    expect(inspector).toHaveTextContent("task-common-guidance.md");
    fireEvent.click(within(inspector).getByRole("tab", { name: "Tools" }));
    expect(inspector).toHaveTextContent("kali.exec");
    expect(inspector).toHaveTextContent("调用 1 次");
  });
});
