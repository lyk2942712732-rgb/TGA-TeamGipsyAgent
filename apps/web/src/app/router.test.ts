import { describe, expect, it } from "vitest";
import { readRoute } from "./router";

describe("readRoute", () => {
  it("covers the complete product navigation", () => {
    expect(readRoute("/tasks")).toEqual({ page: "tasks" });
    expect(readRoute("/approvals")).toEqual({ page: "approvals" });
    expect(readRoute("/resources")).toEqual({ page: "resources" });
    expect(readRoute("/reports")).toEqual({ page: "reports" });
    expect(readRoute("/knowledge-bases")).toEqual({ page: "knowledge-bases" });
    expect(readRoute("/settings/teams")).toEqual({ page: "teams" });
    expect(readRoute("/settings/solvers")).toEqual({ page: "solvers" });
    expect(readRoute("/settings/tools")).toEqual({ page: "tools" });
    expect(readRoute("/settings/policies")).toEqual({ page: "policies" });
    expect(readRoute("/system")).toEqual({ page: "system" });
  });

  it("separates task detail, runtime and replay while decoding stable ids", () => {
    expect(readRoute("/tasks/task%20one")).toEqual({ page: "task-detail", taskId: "task one" });
    expect(readRoute("/tasks/task%20one/runtime")).toEqual({ page: "runtime", taskId: "task one" });
    expect(readRoute("/tasks/task%20one/replay")).toEqual({ page: "replay", taskId: "task one" });
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

  it("does not register or redirect legacy Session URLs", () => {
    expect(readRoute("/sessions/task%20one")).toEqual({ page: "not-found" });
    expect(readRoute("/sessions/task%20one/replay")).toEqual({ page: "not-found" });
  });

  it("rejects paths outside the exact formal route table", () => {
    expect(readRoute("/tasks/new/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/tasks/task-one/runtime/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/tasks/task-one/replay/extra")).toEqual({ page: "not-found" });
    expect(readRoute("/settings/models/extra")).toEqual({ page: "not-found" });
  });
});
