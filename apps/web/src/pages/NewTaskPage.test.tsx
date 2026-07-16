import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NewTaskPage } from "./NewTaskPage";

describe("NewTaskPage", () => {
  it("creates an Agent Session without legacy scope or execution-policy switches", () => {
    render(<NewTaskPage onCreated={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "新建 Session" })).toBeInTheDocument();
    expect(screen.getByLabelText("目标地址或路径")).toBeInTheDocument();
    expect(screen.getByLabelText("初始 Hint（可选）")).toBeInTheDocument();
    expect(screen.queryByText("执行强度")).toBeNull();
    expect(screen.queryByText(/授权范围/)).toBeNull();
    expect(screen.queryByText(/主动探测/)).toBeNull();
    expect(screen.queryByText(/证书校验例外/)).toBeNull();
  });
});
