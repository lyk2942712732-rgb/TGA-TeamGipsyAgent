import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ResizableWorkspace } from "./ResizableWorkspace";

describe("ResizableWorkspace", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }) });
  });

  it("uses reachable tabs at the 1024px compact breakpoint", () => {
    render(<ResizableWorkspace board={<div>策略板内容</div>} timeline={<div>时间线内容</div>} evidence={<div>证据内容</div>} />);
    expect(screen.getByText("时间线内容")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "策略板" }));
    expect(screen.getByText("策略板内容")).toBeInTheDocument();
    expect(screen.queryByText("时间线内容")).not.toBeInTheDocument();
  });

  it("keeps three keyboard-accessible resize separators on desktop", () => {
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }) });
    render(<ResizableWorkspace board={<div>策略板内容</div>} timeline={<div>时间线内容</div>} evidence={<div>证据内容</div>} />);
    expect(screen.getAllByRole("separator")).toHaveLength(2);
  });
});
