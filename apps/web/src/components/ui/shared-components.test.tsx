import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "../../shared/StatusBadge";
import { statusLabel } from "../../shared/status";
import { DataTable } from "./DataTable";
import { EmptyState } from "./EmptyState";
import { PageHeader } from "./PageHeader";
import { RiskBadge } from "./RiskBadge";

describe("shared product UI", () => {
  it("uses one textual status dictionary for task and evidence states", () => {
    render(<><StatusBadge value="awaiting_user_input" /><StatusBadge value="conflict" /><RiskBadge value="destructive" /></>);
    expect(screen.getByText("等待用户输入")).toBeInTheDocument();
    expect(screen.getByText("存在冲突")).toBeInTheDocument();
    expect(screen.getByText("破坏性")).toBeInTheDocument();
    expect(statusLabel("archived")).toBe("已归档");
  });

  it("renders page hierarchy and a reusable empty table state", () => {
    render(<><PageHeader title="任务" description="任务列表" breadcrumbs={[{ label: "TGA", href: "/" }, { label: "任务" }]} /><DataTable label="任务列表" rows={[]} rowKey={() => "none"} columns={[]} emptyLabel="暂无任务" /><EmptyState title="没有结果" description="调整筛选条件" /></>);
    expect(screen.getByRole("navigation", { name: "面包屑" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "任务" })).toBeInTheDocument();
    expect(screen.getByText("暂无任务")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "没有结果" })).toBeInTheDocument();
  });
});
