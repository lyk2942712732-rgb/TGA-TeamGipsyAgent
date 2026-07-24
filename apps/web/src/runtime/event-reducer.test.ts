import { describe, expect, it } from "vitest";
import { applyRuntimeEvent, mergeEvents, runtimeEventNeedsSnapshot } from "./event-reducer";
import type { RuntimeEvent, RuntimeSnapshot } from "./event-types";

const event = (seq: number, type: string, payload: RuntimeEvent["payload"] = {}): RuntimeEvent => ({ id: `event-${seq}`, task_id: "task", seq, type, payload, created_at: "2026-07-23T00:00:00Z" });
const snapshot = (): RuntimeSnapshot => ({ task: { id: "task", name: "task", mode: "ctf", prompt: "verify target", files: [], task_entry_url: "http://target" }, session: { status: "running", turn_count: 0, max_turns: 8 }, solvers: [], challenge: { status: "active", status_reason: "" }, runtime: { memory: [], strategy_cards: [{ id: "card", task_id: "task", title: "card", summary: "", claims: [], prerequisites: [], target_version_checks: [], status: "testing", active_step_id: "step", sources: [], steps: [{ id: "step", title: "read", instructions: "", expected_request: "", success_marker: "artifact", failure_conditions: [], risk: "passive", status: "testing", action_ids: [], evidence_artifact_ids: [], last_result: "" }] }] }, actions: [], flags: [], findings: [], artifacts: [], events: [], latest_seq: 0 });

describe("runtime event reducer", () => {
  it("is idempotent and applies ordered action and strategy updates", () => {
    const events = [event(1, "TOOL_EXECUTION_START", { action_id: "action", tool_name: "input_read", turn: 1 }), event(2, "TOOL_EXECUTION_END", { action_id: "action", status: "succeeded", summary: "read", artifact_ids: ["artifact"], turn: 1 }), event(3, "STRATEGY_STEP_UPDATED", { strategy_card_id: "card", strategy_step_id: "step", status: "succeeded", card_status: "succeeded", active_step_id: null, action_id: "action", artifact_ids: ["artifact"], turn: 1 })];
    const merged = mergeEvents(snapshot(), events);
    expect(merged.actions[0]).toMatchObject({ status: "succeeded", artifact_ids: ["artifact"] });
    expect(merged.runtime.strategy_cards[0].steps[0]).toMatchObject({ status: "succeeded", action_ids: ["action"], evidence_artifact_ids: ["artifact"] });
    expect(merged.runtime.strategy_cards[0]).toMatchObject({ status: "succeeded", active_step_id: null });
    expect(applyRuntimeEvent(merged, events[2])).toBe(merged);
  });

  it("adds task-owned Artifacts from the incremental event stream once", () => {
    const artifact = { id: "artifact_123456789abc", task_id: "task", kind: "file", path: "proof.txt", sha256: "a".repeat(64), tool: "input_read", input_id: "asset_input", provenance: { source: "user_upload" } };
    const saved = event(1, "ARTIFACT_SAVED", { artifact_id: artifact.id, artifact });

    const first = applyRuntimeEvent(snapshot(), saved);
    const repeated = applyRuntimeEvent(first, saved);

    expect(first.artifacts).toEqual([artifact]);
    expect(repeated.artifacts).toEqual([artifact]);
  });

  it("preserves unknown events without changing supported state", () => {
    const current = snapshot(); const next = applyRuntimeEvent(current, event(1, "FUTURE_EVENT", { future: true }));
    expect(next.latest_seq).toBe(1); expect(next.events[0].type).toBe("FUTURE_EVENT"); expect(next.session.status).toBe("running");
  });

  it("projects approval, rejection, flag, challenge and user hint events", () => {
    const next = mergeEvents(snapshot(), [
      event(1, "ACTION_AWAITING_APPROVAL", { action_id: "approval", capability: "http.request" }),
      event(2, "MANAGER_DECISION", { action_id: "approval", decision: "denied", reason: "outside policy" }),
      event(3, "FLAG_CONFIRMED", { value: "CTF{ok}", evidence_artifact_id: "proof" }),
      event(4, "CHALLENGE_STATUS_CHANGED", { status: "solved", reason: "verified", completion_proof_artifact_id: "proof" }),
      event(5, "USER_HINT", { memory_id: "hint-1", content: "try the archive" }),
    ]);
    expect(next.session.status).toBe("awaiting_approval");
    expect(next.actions[0]).toMatchObject({ status: "rejected", target: "http://target" });
    expect(next.flags).toEqual([{ value: "CTF{ok}", evidence_artifact_id: "proof", created_at: "2026-07-23T00:00:00Z" }]);
    expect(next.challenge).toMatchObject({ status: "solved", status_reason: "verified", completion_proof_artifact_id: "proof" });
    expect(next.runtime.memory[0]).toMatchObject({ id: "hint-1", kind: "hint", content: "try the archive", source: "user" });
  });

  it("projects complete approval details and closes resolved actions", () => {
    const approval = applyRuntimeEvent(snapshot(), event(1, "ACTION_AWAITING_APPROVAL", {
      action_id: "approval", capability: "http.request", target: "https://target/item", risk: "destructive",
      rationale: "remove fixture", expected_outcome: "fixture removed", alternative_analysis: "GET cannot validate deletion",
      effect: { scope: "target", persistence: "persistent", reversibility: "irreversible", category: "resource_delete", description: "Delete the fixture" },
      approval_expires_at: "2026-07-23T00:15:00Z", arguments: { method: "DELETE", body: { present: true } },
    }));
    expect(approval.actions[0]).toMatchObject({
      status: "pending_approval", capability: "http.request", target: "https://target/item", risk: "destructive",
      alternative_analysis: "GET cannot validate deletion", approval_expires_at: "2026-07-23T00:15:00Z",
      effect: { scope: "target", persistence: "persistent", category: "resource_delete" },
      arguments: { method: "DELETE", body: { present: true } },
    });
    expect(applyRuntimeEvent(approval, event(2, "ACTION_APPROVAL_EXPIRED", { action_id: "approval" })).actions[0].status).toBe("rejected");
    expect(applyRuntimeEvent(approval, event(2, "ACTION_CANCELLED", { action_id: "approval", reason: "user_cancelled" })).actions[0]).toMatchObject({ status: "cancelled", summary: "user_cancelled" });
  });

  it("requests an authoritative snapshot for incomplete and terminal projections", () => {
    expect(runtimeEventNeedsSnapshot(event(1, "MEMORY_UPSERTED", { memory_id: "known" }))).toBe(true);
    expect(runtimeEventNeedsSnapshot(event(2, "MEMORY_UPSERTED", { memory: { id: "known", kind: "fact", content: "full", artifact_ids: [], source: "runtime" } }))).toBe(false);
    expect(runtimeEventNeedsSnapshot(event(3, "STRATEGY_CARD_CREATED", { strategy_card_id: "card-2" }))).toBe(true);
    expect(runtimeEventNeedsSnapshot(event(4, "AGENT_FINISHED"))).toBe(true);
  });
});
