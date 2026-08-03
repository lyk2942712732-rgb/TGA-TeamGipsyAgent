import { normalizeRuntimeSnapshot } from "./models/normalize";
import type { RuntimeStore } from "./models/types";

export function workbenchStore(mode = "ctf"): RuntimeStore {
  return normalizeRuntimeSnapshot({
    schema_version: 6,
    task_common_skill_snapshot: { selector: "task-common-v1", skills: [{ name: "evidence-method", version: "1", content_sha256: "abc", selection_reasons: ["task common guidance"] }] },
    task: {
      id: "task", name: `${mode} task`, mode, goal: "verify the target",
      session_input: { prompt: "inspect supplied evidence", files: [{ id: "input-a", original_name: "sample.bin", kind: "task_input", size: 12 }] },
      mode_config: { scope: ["target.test"], rules_of_engagement: ["read only"], success_criteria: ["confirmed evidence"] },
    },
    session: { status: "running", supervisor_solver_id: "supervisor", active_solver_count: 4, max_active_workers: 2, task_budget_usage: { turns: 12, input_tokens: 100, output_tokens: 40, tool_calls: 8, artifacts: 3 }, stop_reason: null, timestamps: { started_at: "2026-07-30T00:00:00Z" }, turn_count: 12, max_turns: 40 },
    team: { task_id: "task", status: "running", supervisor_solver_id: "supervisor", max_active_workers: 2, max_total_solvers: 8, active_solver_count: 4, solver_ids: ["supervisor", "worker-running", "worker-approval", "reviewer", "reporter"], version: 4, timestamps: {} },
    solvers: [
      solver("supervisor", "supervisor", "running", null, null, ["planning"], { turns: 3, input_tokens: 20, tool_calls: 1 }),
      solver("worker-running", "worker", "running", "supervisor", "intent-running", ["web"], { turns: 4, input_tokens: 35, tool_calls: 3 }),
      solver("worker-approval", "worker", "awaiting_approval", "supervisor", "intent-approval", ["binary"], { turns: 2, input_tokens: 20, tool_calls: 2 }),
      solver("reviewer", "reviewer", "waiting", "supervisor", "intent-review", ["evidence-review"], { turns: 2, input_tokens: 15, tool_calls: 1 }),
      solver("reporter", "reporter", "completed", "supervisor", "intent-report", ["reporting"], { turns: 1, input_tokens: 10, tool_calls: 1 }),
    ],
    intents: [
      intent("intent-running", "Inspect live target", "running", "worker-running", []),
      intent("intent-approval", "Publish binary result", "awaiting_approval", "worker-approval", []),
      intent("intent-review", "Resolve reviewer conflict", "reviewing", "reviewer", ["intent-running"]),
      intent("intent-report", "Write report", "completed", "reporter", ["intent-review"]),
      intent("intent-pending", "Validate completion", "pending", null, ["intent-review"]),
    ],
    worker_results: [{ result_id: "result-running", solver_id: "worker-running", intent_id: "intent-running", status: "submitted", summary: "HTTP surface mapped", artifact_ids: ["artifact-http"], evidence_claim_ids: ["claim-http"], knowledge_ids: ["knowledge-verified"], finding_ids: ["finding-http"], limitations: [], budget_usage: { tool_calls: 3 } }],
    global_plan: { version: 4, success_criteria: ["confirmed evidence"] },
    knowledge: [
      { knowledge_id: "knowledge-verified", scope: "task", target_id: null, status: "verified", kind: "fact", content_preview: "Endpoint returns version metadata", content_sha256: "a", created_by_solver_id: "worker-running", created_at: "" },
      { knowledge_id: "knowledge-candidate", scope: "solver", target_id: "worker-approval", status: "candidate", kind: "hypothesis", content_preview: "Candidate binary offset", content_sha256: "b", created_by_solver_id: "worker-approval", created_at: "" },
      { knowledge_id: "knowledge-conflict", scope: "task", target_id: null, status: "conflict", kind: "conflict", content_preview: "Reviewer found conflicting version evidence", content_sha256: "c", created_by_solver_id: "reviewer", created_at: "" },
      { knowledge_id: "knowledge-rejected", scope: "intent", target_id: "intent-running", status: "rejected", kind: "fact", content_preview: "Rejected guess", content_sha256: "d", created_by_solver_id: "worker-running", created_at: "" },
    ],
    artifacts: [
      { artifact_id: "artifact-http", intent_id: "intent-running", kind: "tool_output", media_type: "application/json", tool: "http.request", target: "https://target.test", sha256: "a", created_at: "" },
      { artifact_id: "artifact-binary", intent_id: "intent-approval", kind: "solver_publication", media_type: "application/octet-stream", tool: "workspace.publish", target: "shared/output.bin", sha256: "b", created_at: "" },
    ],
    evidence_claims: [{ claim_id: "claim-http", statement_preview: "Version endpoint proves product version", artifact_id: "artifact-http", locator: { kind: "json_path", path: "$.version" }, status: "confirmed", created_by_solver_id: "worker-running", reviewed_by_solver_id: "reviewer", created_at: "", reviewed_at: "" }],
    findings: [{ finding_id: "finding-http", title: "Version exposed", description_preview: "Version metadata is public", target: "target.test", severity: "medium", status: "confirmed", evidence_claim_ids: ["claim-http"], created_by_solver_id: "worker-running", created_at: "", reviewed_at: "" }],
    actions: [
      { id: "action-write", action_id: "action-write", solver_id: "worker-approval", intent_id: "intent-approval", capability: "artifact.publish", target: "shared/output.bin", risk: "active", effect: { persistence: "persistent", reversibility: "reversible", description: "Publish result" }, arguments: {}, status: "pending_approval", summary: "", artifact_ids: [], created_at: "", updated_at: "" },
    ],
    approvals: [
      { approval_id: "approval-write", solver_id: "worker-approval", intent_id: "intent-approval", action_id: "action-write", action: { capability: "artifact.publish", target: "shared/output.bin", expected_outcome: "Published result" }, risk: "active", effect: { persistence: "persistent", reversibility: "reversible", description: "Publish result" }, reason: "Persistent shared write", alternatives: ["Preview only"], deadline: "2026-07-31T00:00:00Z", status: "pending", created_at: "", updated_at: "" },
      { approval_id: "approval-http", solver_id: "worker-running", intent_id: "intent-running", action_id: "action-http", action: { capability: "http.request", target: "https://target.test/admin", expected_outcome: "Confirm access" }, risk: "active", effect: { persistence: "none", reversibility: "not_applicable", description: "Request admin endpoint" }, reason: "Active target request", alternatives: ["Use cached evidence"], deadline: "2026-07-31T01:00:00Z", status: "pending", created_at: "", updated_at: "" },
    ],
    retrieval_runs: [{ retrieval_run_id: "retrieval-one", owner_scope: "task", workspace_id: null, task_id: "task", solver_id: "worker-running", intent_id: "intent-running", index_snapshot_id: "index-one", method: "keyword", query_preview: "product version", hit_count: 3, created_at: "" }],
    events: [
      runtimeEvent(1, "ORCHESTRATOR_STARTED", "supervisor", null, { supervisor_solver_id: "supervisor" }),
      runtimeEvent(2, "INTENT_CLAIMED", "worker-running", "intent-running", { intent_id: "intent-running", solver_id: "worker-running", turn: 1 }),
      runtimeEvent(3, "TOOL_EXECUTION_END", "worker-running", "intent-running", { tool_name: "http.request", action_id: "action-http-read", summary: "Mapped HTTP surface", turn: 1 }),
      runtimeEvent(4, "APPROVAL_REQUESTED", "worker-approval", "intent-approval", { approval_id: "approval-write", action_id: "action-write", reason: "Persistent shared write", turn: 2 }),
      runtimeEvent(5, "KNOWLEDGE_CONFLICT_DETECTED", "reviewer", "intent-review", { conflict_id: "knowledge-conflict", summary: "Conflicting version evidence", turn: 1 }),
      runtimeEvent(6, "SOLVER_COMPLETED", "reporter", "intent-report", { summary: "Report draft prepared", turn: 1 }),
    ],
    events_page: { after_seq: 0, next_after_seq: 6, has_more: false }, latest_seq: 6,
  });
}

function solver(id: string, role: string, status: string, parent: string | null, assigned: string | null, specialties: string[], usage: Record<string, number>) {
  return { task_id: "task", solver_id: id, definition_id: `${role}-v1`, orchestration_role: role, specialties, parent_solver_id: parent, assigned_intent_id: assigned, status, current_summary: `${id} current activity`, model_snapshot: { model: "test-model" }, skill_snapshot: { count: 2, names: [`${role}-method`, "task-common"], selector: "v1", total_chars: 100 }, capability_binding: { host_capability_ids: ["input.read", "artifact.inspect"], kali: { profile_id: "ctf-base", capabilities: ["kali.exec"] }, content_sha256: "hash" }, budget_usage: usage, timestamps: { started_at: "2026-07-30T00:00:00Z" } };
}
function intent(id: string, title: string, status: string, assigned: string | null, dependencies: string[]) { return { task_id: "task", intent_id: id, kind: "investigate", title, objective: title, status, assigned_solver_id: assigned, dependencies, priority: 1, budget: {}, created_at: "", updated_at: "" }; }
function runtimeEvent(seq: number, type: string, solverId: string | null, intentId: string | null, payload: Record<string, unknown>) { return { schema_version: 6, id: `event-${seq}`, task_id: "task", seq, type, solver_id: solverId, intent_id: intentId, payload: { payload_version: 1, ...payload }, created_at: `2026-07-30T00:00:0${seq}Z` }; }
