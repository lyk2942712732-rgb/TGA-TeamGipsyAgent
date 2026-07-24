import { describe, expect, it } from "vitest";
import { readRoute } from "./router";

describe("readRoute", () => {
  it("decodes valid task ids", () => expect(readRoute("/tasks/task%20one/runtime")).toEqual({ page: "runtime", taskId: "task one" }));
  it("falls back instead of throwing on malformed escapes", () => expect(readRoute("/tasks/%E0%A4%A/runtime")).toEqual({ page: "dashboard" }));
});
