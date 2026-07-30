import { describe, expect, it } from "vitest";
import { readRoute, runtimePageVariant } from "./router";

describe("readRoute", () => {
  it("decodes valid task ids", () => expect(readRoute("/tasks/task%20one/runtime")).toEqual({ page: "runtime", taskId: "task one" }));
  it("falls back instead of throwing on malformed escapes", () => expect(readRoute("/tasks/%E0%A4%A/runtime")).toEqual({ page: "dashboard" }));
  it("routes System Prompt independently from Skills", () => {
    expect(readRoute("/settings/skills")).toEqual({ page: "skills" });
    expect(readRoute("/settings/system-prompt")).toEqual({ page: "system-prompt" });
  });
  it("keeps the legacy Runtime page available behind an explicit feature flag", () => {
    expect(runtimePageVariant("legacy")).toBe("legacy");
    expect(runtimePageVariant(undefined)).toBe("task");
  });
});
