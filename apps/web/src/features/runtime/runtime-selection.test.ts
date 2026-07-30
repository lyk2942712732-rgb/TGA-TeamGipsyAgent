import { describe, expect, it } from "vitest";
import { readRuntimeSelection, writeRuntimeSelection } from "./runtime-selection";

describe("runtime URL selection", () => {
  it("restores solver, intent and workspace tab from a deep link", () => {
    expect(readRuntimeSelection("?solver=worker-a&intent=intent-a&tab=timeline")).toEqual({
      solverId: "worker-a", intentId: "intent-a", tab: "timeline",
    });
  });

  it("preserves unrelated selection while updating one field", () => {
    expect(writeRuntimeSelection("?solver=worker-a&intent=intent-a", { tab: "evidence" })).toBe("?solver=worker-a&intent=intent-a&tab=evidence");
    expect(writeRuntimeSelection("?solver=worker-a&intent=intent-a&tab=evidence", { solverId: null })).toBe("?intent=intent-a&tab=evidence");
  });

  it("falls back safely for unknown tabs and overlong identifiers", () => {
    expect(readRuntimeSelection("?tab=future&solver=" + "x".repeat(300))).toEqual({ solverId: null, intentId: null, tab: "overview" });
  });
});
