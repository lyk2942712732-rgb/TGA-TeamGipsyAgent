import { describe, expect, it } from "vitest";
import { readRoute } from "./router";

describe("readRoute", () => {
  it("covers the complete product navigation", () => {
    expect(readRoute("/tasks")).toEqual({ page: "tasks" });
    expect(readRoute("/approvals")).toEqual({ page: "approvals" });
    expect(readRoute("/reports")).toEqual({ page: "reports" });
    expect(readRoute("/settings/solvers")).toEqual({ page: "solvers" });
    expect(readRoute("/settings/skills")).toEqual({ page: "skills" });
    expect(readRoute("/settings/models")).toEqual({ page: "models" });
    expect(readRoute("/settings/policies")).toEqual({ page: "policies" });
    expect(readRoute("/system")).toEqual({ page: "system" });
  });

  it("routes both task links directly to the native runtime", () => {
    expect(readRoute("/tasks/task%20one")).toEqual({ page: "runtime", taskId: "task one" });
    expect(readRoute("/tasks/task%20one/runtime")).toEqual({ page: "runtime", taskId: "task one" });
    expect(readRoute("/tasks/task%20one/replay")).toEqual({ page: "not-found" });
  });

  it("falls back instead of throwing on malformed escapes", () => {
    expect(readRoute("/tasks/%E0%A4%A/runtime")).toEqual({ page: "not-found" });
  });

  it("keeps only the formal Settings routes", () => {
    expect(readRoute("/settings/skills")).toEqual({ page: "skills" });
    expect(readRoute("/settings/models")).toEqual({ page: "models" });
    expect(readRoute("/settings/capabilities")).toEqual({ page: "not-found" });
    expect(readRoute("/settings/system-prompt")).toEqual({ page: "not-found" });
  });

  it("rejects paths outside the exact formal route table", () => {
    expect(readRoute("/tasks/new/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/tasks/task-one/runtime/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/unknown/task-one/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/settings/models/extra")).toEqual({ page: "not-found" });
  });
});
