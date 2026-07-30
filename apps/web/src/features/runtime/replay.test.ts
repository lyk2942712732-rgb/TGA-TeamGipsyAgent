import { describe, expect, it } from "vitest";
import { replayStoreAtSeq } from "./models/replay";
import { workbenchStore } from "./workbench-test-support";

describe("Phase 11 replay projection", () => {
  it("restores Solver, Intent and Approval state at the selected sequence", () => {
    const source = workbenchStore();
    const beforeApproval = replayStoreAtSeq(source, 3);
    const afterApproval = replayStoreAtSeq(source, 4);
    expect(beforeApproval.latestSeq).toBe(3);
    expect(beforeApproval.solversById["worker-running"].status).toBe("running");
    expect(Object.keys(beforeApproval.approvalsById)).toHaveLength(0);
    expect(afterApproval.approvalsById["approval-write"]).toMatchObject({ actionId: "action-write", status: "pending" });
  });

  it("supports the v5 legacy reducer without enabling mutations", () => {
    const legacy = workbenchStore();
    const value = { ...legacy, schemaVersion: 5 as const, legacy: true };
    expect(replayStoreAtSeq(value, 2).legacy).toBe(true);
  });
});
