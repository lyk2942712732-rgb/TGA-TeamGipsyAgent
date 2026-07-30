import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScenePanel } from "./scenes/ScenePanel";
import { workbenchStore } from "./workbench-test-support";

describe.each([
  ["ctf", "候选 Flag"],
  ["penetration_test", "Coverage Matrix"],
  ["incident_response", "Evidence Preservation"],
  ["vulnerability_research", "Root Cause"],
  ["reverse_engineering", "Function / Call Graph"],
])("%s scene", (mode, label) => {
  it("uses the shared shell and renders a projection-only specialized view", () => {
    render(<ScenePanel store={workbenchStore(mode)} />);
    expect(screen.getByTestId("scene-shell")).toHaveTextContent(label);
    expect(screen.getByTestId("scene-shell")).toHaveTextContent("后端投影");
  });
});
