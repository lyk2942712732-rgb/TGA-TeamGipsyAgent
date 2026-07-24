import { expect, test, type Page } from "@playwright/test";

const task = { id: "task_1", name: "本地证据任务", mode: "ctf", task_entry_url: null, session_input: { prompt: "读取本地证据", files: [] } };
const snapshot = {
  task,
  session: { status: "completed", turn_count: 2, max_turns: 8, stop_reason: "finish_accepted", started_at: "2026-07-23T00:00:00Z", finished_at: "2026-07-23T00:00:04Z" },
  solvers: [{ id: "agent_1", role: "main", status: "completed", model_name: "provider-model" }],
  challenge: { status: "solved", status_reason: "completion_validator" },
  runtime: {
    memory: [{ id: "memory_1", kind: "hint", content: "先读取任务输入，再提交证据。", artifact_ids: [], source: "user" }],
    strategy_cards: [{
      id: "card_1", task_id: "task_1", title: "读取并验证本地输入", summary: "使用输入工具生成任务证据", claims: [], prerequisites: [], target_version_checks: [], status: "succeeded", active_step_id: null, sources: [],
      steps: [{ id: "step_1", title: "读取输入", instructions: "", expected_request: "input_read", success_marker: "task-owned Artifact", failure_conditions: [], risk: "passive", status: "succeeded", action_ids: ["act_1"], evidence_artifact_ids: ["artifact_1"], last_result: "读取成功" }],
    }],
  },
  actions: [{ id: "act_1", capability: "input_read", target: "input", status: "succeeded", risk: "passive", strategy_card_id: "card_1", strategy_step_id: "step_1", rationale: "读取用户提供的本地文件", expected_outcome: "生成任务证据", artifact_ids: ["artifact_1"], authorization: { allowed: true }, summary: "读取成功" }],
  artifacts: [{ id: "artifact_1", kind: "tool_output", path: "artifact.txt", sha256: "1234567890abcdef1234", tool: "input_read", target: "input", provenance: { source: "user_upload" } }],
  flags: [{ value: "CTF{evidence_backed}", evidence_artifact_id: "artifact_1" }], findings: [], artifact_indexes: [], context_metrics: [{ turn: 2, audit_message_count: 5, working_message_count: 4, working_chars: 2048, summary_hits: 0, artifact_retrievals: 1, provider_input_tokens: 120, provider_output_tokens: 32 }],
  events: [
    { id: "evt_1", task_id: "task_1", seq: 1, type: "MESSAGE_START", payload: { turn: 1 }, created_at: "2026-07-23T00:00:01Z" },
    { id: "evt_2", task_id: "task_1", seq: 2, type: "FINISH_REJECTED", payload: { turn: 1, validator_code: "EVIDENCE_REQUIRED", missing: ["task-owned Artifact"] }, created_at: "2026-07-23T00:00:02Z" },
    { id: "evt_3", task_id: "task_1", seq: 3, type: "MESSAGE_START", payload: { turn: 2 }, created_at: "2026-07-23T00:00:03Z" },
    { id: "evt_4", task_id: "task_1", seq: 4, type: "TOOL_EXECUTION_START", payload: { turn: 2, action_id: "act_1", tool_name: "input_read", execution_location: "Input Store" }, created_at: "2026-07-23T00:00:03Z" },
    { id: "evt_5", task_id: "task_1", seq: 5, type: "TOOL_EXECUTION_END", payload: { turn: 2, action_id: "act_1", tool_name: "input_read", status: "succeeded", summary: "读取成功", execution_location: "Input Store", artifact_ids: ["artifact_1"] }, created_at: "2026-07-23T00:00:04Z" },
    { id: "evt_6", task_id: "task_1", seq: 6, type: "FINISH_ACCEPTED", payload: { turn: 2, summary: "已完成", evidence_artifact_ids: ["artifact_1"], terminal: true }, created_at: "2026-07-23T00:00:04Z" },
    { id: "evt_7", task_id: "task_1", seq: 7, type: "AGENT_FINISHED", payload: { turn: 2, summary: "已完成", coverage: ["本地输入"], limitations: ["无网络目标"], evidence_artifact_ids: ["artifact_1"] }, created_at: "2026-07-23T00:00:04Z" },
  ], latest_seq: 7, schema_version: 5,
};

async function mockRuntime(page: Page) {
  await page.route("**/api/v2/settings/llm", (route) => route.fulfill({ json: { configured: true, model: "provider-model" } }));
  await page.route("**/api/v2/tasks", (route) => route.fulfill({ json: { tasks: [{ task_id: "task_1", name: task.name, mode: "ctf", task_entry_url: null, target_summary: "本地输入任务", created_at: "2026-07-23T00:00:00Z", status: "completed", flags: 1, findings: 0, artifacts: 1 }] } }));
  await page.route("**/api/v2/tasks/task_1/session", (route) => route.fulfill({ json: snapshot }));
  await page.route("**/api/v2/tasks/task_1/events?*", (route) => route.fulfill({ json: { events: [], latest_seq: 7 } }));
  await page.route("**/api/v2/tasks/task_1/events/stream?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {\"latest_seq\":7}\n\n" }));
  await page.route("**/api/v2/tasks/task_1/hints", (route) => route.fulfill({ json: { accepted: true } }));
  await page.route("**/api/v2/tasks/task_1/control", (route) => route.fulfill({ json: { accepted: true } }));
}

test("runtime renders turn-grouped ReAct execution and confirmed evidence", async ({ page }) => {
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await expect(page.getByRole("heading", { name: task.name })).toBeVisible();
  await expect(page.getByRole("heading", { name: "模型决策与真实执行" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "策略与记忆" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "证据与结果" })).toBeVisible();
  await expect(page.getByTestId("react-turn")).toHaveCount(2);
  await page.getByRole("button", { name: /TURN 01/ }).click();
  await expect(page.getByLabel("ReAct 回合时间线").getByText("task-owned Artifact")).toBeVisible();
  await expect(page.getByTestId("execution-location").first()).toContainText("Input Store");
  await page.getByRole("button", { name: "最终结果" }).click();
  await expect(page.getByTestId("final-result")).toContainText("已确认最终结果");
  await expect(page.getByText("CTF{evidence_backed}")).toBeVisible();
});

test("hint is persisted as EvidenceMemory guidance and replay is read-only", async ({ page }) => {
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await page.getByRole("button", { name: "补充提示" }).click();
  await page.getByLabel("补充提示").fill("已知失败边界");
  await page.getByRole("button", { name: "提交提示" }).click();
  await expect(page.getByText("提示已写入 EvidenceMemory，并关联新的 StrategyCard。")).toBeVisible();
  await page.getByRole("button", { name: "回放" }).click();
  await expect(page.getByText(/回放模式只读取已持久化 Snapshot/)).toBeVisible();
  await expect(page.getByRole("button", { name: /取消/ })).toHaveCount(0);
});

test("runtime exposes a recoverable blocked error and resumes the session", async ({ page }) => {
  await mockRuntime(page);
  const blocked = structuredClone(snapshot);
  blocked.session = { ...blocked.session, status: "blocked", stop_reason: "ISOLATED_RUNTIME_UNAVAILABLE" };
  blocked.events = [
    ...blocked.events,
    { id: "evt_blocked", task_id: "task_1", seq: 8, type: "RUNTIME_ERROR", payload: { code: "ISOLATED_RUNTIME_UNAVAILABLE", phase: "process", message: "Docker runtime is unavailable", retryable: true }, created_at: "2026-07-23T00:00:05Z" },
  ];
  let controls = 0;
  await page.route("**/api/v2/tasks/task_1/session", (route) => route.fulfill({ json: blocked }));
  await page.route("**/api/v2/tasks/task_1/control", (route) => {
    controls += 1;
    return route.fulfill({ json: { accepted: true } });
  });

  await page.goto("/tasks/task_1/runtime");
  await expect(page.getByRole("alert", { name: "运行时错误" })).toContainText("Docker runtime is unavailable");
  await expect(page.getByRole("alert", { name: "运行时错误" })).toContainText("确认 Docker 隔离运行时和镜像可用");
  await page.getByRole("alert", { name: "运行时错误" }).getByRole("button", { name: "恢复会话" }).click();
  await expect.poll(() => controls).toBe(1);
});

test("runtime renders a durable approval and sends the explicit decision", async ({ page }) => {
  await mockRuntime(page);
  const awaitingApproval = structuredClone(snapshot);
  awaitingApproval.session = { ...awaitingApproval.session, status: "awaiting_approval" };
  awaitingApproval.actions = [{
    id: "approval_1", capability: "http.request", target: "https://challenge.example/resource", actual_target: "https://challenge.example/resource", status: "pending_approval", risk: "active",
    rationale: "删除测试资源", expected_outcome: "资源被删除", alternative_analysis: "GET 无法验证删除行为", artifact_ids: [],
    arguments: { method: "DELETE", body: { token: "redacted" } }, approval_expires_at: "2099-01-01T00:00:00Z",
    effect: { scope: "target", persistence: "persistent", reversibility: "irreversible", category: "resource_delete", description: "删除测试资源" },
  }];
  let approval: { action?: string; action_id?: string } | undefined;
  await page.route("**/api/v2/tasks/task_1/session", (route) => route.fulfill({ json: awaitingApproval }));
  await page.route("**/api/v2/tasks/task_1/control", (route) => {
    approval = route.request().postDataJSON() as { action?: string; actionId?: string };
    return route.fulfill({ json: { accepted: true } });
  });

  await page.goto("/tasks/task_1/runtime");
  await expect(page.getByRole("region", { name: "高影响操作审批" })).toContainText("DELETE");
  await page.getByRole("button", { name: "批准并执行" }).click();
  await expect.poll(() => approval).toMatchObject({ action: "approve_action", action_id: "approval_1" });
});

for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
  test(`runtime has no horizontal overlap at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockRuntime(page);
    await page.goto("/tasks/task_1/runtime");
    await expect(page.getByRole("heading", { name: task.name })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
