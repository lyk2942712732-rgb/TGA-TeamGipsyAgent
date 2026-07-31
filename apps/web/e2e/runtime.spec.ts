import { expect, test, type Page } from "@playwright/test";

const modes = [
  ["ctf", "候选 Flag"],
  ["penetration_test", "Coverage Matrix"],
  ["incident_response", "Evidence Preservation"],
  ["vulnerability_research", "Root Cause"],
  ["reverse_engineering", "Function / Call Graph"],
] as const;

function snapshot(mode: string) {
  const taskId = `fixture_${mode}`;
  return {
    schema_version: 6,
    task: {
      id: taskId, name: `${mode} command fixture`, mode,
      goal: "Verify the local fixture without external execution",
      session_input: { prompt: "offline fixture", files: [] },
      mode_config: { mode },
    },
    session: {
      status: "running", supervisor_solver_id: "supervisor", active_solver_count: 2,
      max_active_workers: 2,
      task_budget_usage: { turns: 3, input_tokens: 80, output_tokens: 20, tool_calls: 1, artifacts: 1 },
      stop_reason: null,
      timestamps: { started_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:00:05Z" },
      turn_count: 3, max_turns: 20,
    },
    team: {
      task_id: taskId, status: "running", supervisor_solver_id: "supervisor",
      max_active_workers: 2, max_total_solvers: 8, active_solver_count: 2,
      solver_ids: ["supervisor", "worker"], version: 2,
      timestamps: { created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:00:05Z" },
    },
    solvers: [
      {
        task_id: taskId, solver_id: "supervisor", definition_id: "task-supervisor",
        orchestration_role: "supervisor", specialties: ["planning"], parent_solver_id: null,
        assigned_intent_id: null, status: "running", current_summary: "Coordinating local fixture",
        model_snapshot: {}, skill_snapshot: { names: [] },
        tool_policy: { profile: "supervisor", allowed_capabilities: [] },
        budget_usage: { turns: 1 }, timestamps: {},
      },
      {
        task_id: taskId, solver_id: "worker", definition_id: "fixture-worker",
        orchestration_role: "worker", specialties: ["offline-analysis"], parent_solver_id: "supervisor",
        assigned_intent_id: "intent_fixture", status: "awaiting_approval",
        current_summary: "Inspecting projected evidence",
        model_snapshot: {}, skill_snapshot: { names: ["fixture-method"] },
        tool_policy: { profile: "offline", allowed_capabilities: ["artifact.inspect"] },
        budget_usage: { turns: 2, input_tokens: 80, output_tokens: 20, tool_calls: 1 }, timestamps: {},
      },
    ],
    intents: [{
      task_id: taskId, intent_id: "intent_fixture", kind: "analysis", title: "Inspect local fixture",
      objective: "Read the bounded local fixture", status: "awaiting_approval",
      assigned_solver_id: "worker", dependencies: [], priority: 1, budget: { turns: 8 },
      created_at: "2026-07-30T00:00:01Z", updated_at: "2026-07-30T00:00:05Z",
    }],
    worker_results: [],
    global_plan: { id: "plan_fixture", status: "active", version: 2, completion_criteria: ["confirmed evidence"] },
    knowledge: [{
      knowledge_id: "knowledge_fixture", scope: "task", target_id: null, status: "verified",
      kind: "fact", content_preview: "Fixture metadata is internally consistent",
      created_by_solver_id: "supervisor", created_at: "2026-07-30T00:00:03Z",
    }],
    artifacts: [{
      artifact_id: "artifact_fixture", intent_id: "intent_fixture", kind: "fixture",
      sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      created_at: "2026-07-30T00:00:03Z", provenance: { source: "offline_fixture" },
    }],
    evidence_claims: [], findings: [], retrieval_runs: [],
    actions: [{
      id: "action_fixture", action_id: "action_fixture", solver_id: "worker",
      intent_id: "intent_fixture", capability: "artifact.publish", target: "shared/fixture.json",
      risk: "active", effect: { description: "Publish a fixture summary", reversibility: "reversible" },
      status: "pending_approval", created_at: "2026-07-30T00:00:04Z", updated_at: "2026-07-30T00:00:04Z",
    }],
    approvals: [{
      approval_id: "approval_fixture", action_id: "action_fixture", solver_id: "worker",
      intent_id: "intent_fixture", status: "pending", reason: "Shared publication requires review",
      alternatives: ["Keep Solver-private"], risk: "active",
      effect: { description: "Publish a fixture summary", reversibility: "reversible" },
      deadline: "2099-01-01T00:00:00Z", created_at: "2026-07-30T00:00:04Z",
    }],
    events: [{
      schema_version: 1, id: "event_fixture", task_id: taskId, solver_id: "worker",
      intent_id: "intent_fixture", seq: 1, type: "APPROVAL_REQUESTED",
      payload: { action_id: "action_fixture", summary: "Fixture publication awaits approval", turn: 2 },
      created_at: "2026-07-30T00:00:04Z",
    }],
    events_page: { after_seq: 0, next_after_seq: 1, has_more: false }, latest_seq: 1,
    challenge: { status: "candidate", status_reason: "offline fixture" },
    flags: [{ value: "CTF{fixture}", evidence_artifact_id: "artifact_fixture" }],
    artifact_indexes: [{ artifact_id: "artifact_fixture", document_type: "fixture", extraction_status: "parsed", summary: "Local fixture metadata", segment_count: 1, source_refs: ["fixture:1"] }],
  };
}

async function mockRuntime(page: Page, mode: string) {
  const taskId = `fixture_${mode}`;
  await page.route(`**/api/v2/tasks/${taskId}/session`, (route) => route.fulfill({ json: snapshot(mode) }));
  await page.route(`**/api/v2/tasks/${taskId}/events?*`, (route) => route.fulfill({ json: { events: [], latest_seq: 1, has_more: false } }));
  await page.route(`**/api/v2/tasks/${taskId}/events/stream?*`, (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {\"latest_seq\":1}\n\n" }));
  await page.route(`**/api/v2/tasks/${taskId}/approvals/*/decision`, (route) => route.fulfill({ json: { accepted: true } }));
  await page.route(`**/api/v2/tasks/${taskId}/interventions`, (route) => route.fulfill({ json: { accepted: true } }));
}

for (const [mode, sceneLabel] of modes) {
  test(`${mode} opens the projection-only command scene`, async ({ page }) => {
    await mockRuntime(page, mode);
    await page.goto(`/tasks/fixture_${mode}/runtime`);
    await expect(page.getByRole("heading", { name: `${mode} command fixture` })).toBeVisible();
    await expect(page.getByRole("tree", { name: "Solver 团队" })).toContainText("worker");
    await expect(page.getByTestId("scene-shell")).toContainText(sceneLabel);
    await expect(page.getByTestId("scene-shell")).toContainText("后端投影");
  });
}

test("approval, scoped intervention and replay remain task-governed", async ({ page }) => {
  await mockRuntime(page, "ctf");
  let decision: unknown;
  let intervention: unknown;
  await page.route("**/api/v2/tasks/fixture_ctf/approvals/*/decision", async (route) => {
    decision = route.request().postDataJSON();
    await route.fulfill({ json: { accepted: true } });
  });
  await page.route("**/api/v2/tasks/fixture_ctf/interventions", async (route) => {
    intervention = route.request().postDataJSON();
    await route.fulfill({ json: { accepted: true } });
  });
  await page.goto("/tasks/fixture_ctf/runtime");
  await page.getByRole("button", { name: /审批中心/ }).click();
  await page.getByRole("button", { name: "批准 action_fixture" }).click();
  await expect.poll(() => decision).toEqual({ decision: "approve" });

  await page.getByRole("button", { name: "补充信息" }).click();
  await page.getByLabel("作用域").selectOption("solver");
  await page.getByLabel("目标 Solver").selectOption("worker");
  await page.getByLabel("类型").selectOption("constraint");
  await page.getByLabel("内容").fill("Keep all work inside the offline fixture");
  await page.getByRole("button", { name: "提交 Intervention" }).click();
  await expect.poll(() => intervention).toEqual({
    kind: "constraint", content: "Keep all work inside the offline fixture",
    scope: "solver", target_id: "worker",
  });

  await page.getByRole("button", { name: "回放" }).click();
  await expect(page).toHaveURL(/\/tasks\/fixture_ctf\/replay/);
  await expect(page.getByRole("slider", { name: "回放序列" })).toBeVisible();
  await expect(page.getByRole("button", { name: "补充信息" })).toHaveCount(0);
});

for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`command workbench has no horizontal overlap at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockRuntime(page, "ctf");
    await page.goto("/tasks/fixture_ctf/runtime");
    await expect(page.getByRole("heading", { name: "ctf command fixture" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
