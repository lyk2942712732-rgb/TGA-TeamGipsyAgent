import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RuntimeTopology } from "./components/RuntimeTopology";
import { workbenchStore } from "./workbench-test-support";

describe("Phase 11 governance UI", () => {
  it("renders the live solver topology and latest transient interaction", () => {
    render(<RuntimeTopology store={workbenchStore()} />);
    expect(screen.getByRole("heading", { name: "运行拓扑" })).toBeInTheDocument();
    expect(screen.getByText("Supervisor")).toBeInTheDocument();
    expect(screen.getByText("Kali / Tools")).toBeInTheDocument();
    expect(screen.getByText("请求批准 工具调用")).toBeInTheDocument();
  });
});
