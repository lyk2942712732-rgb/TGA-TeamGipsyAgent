import { expect, test, type Page } from "@playwright/test";

const task = { id: "task_1", name: "本地 Web CTF", mode: "ctf", target: "http://target.local", scope: ["target.local"] };
const snapshot = {
  task, latest_seq: 6,
  session: { status: "running", turn_count: 1, max_turns: 48 }, solvers: [{ id: "solver_1", role: "main", status: "running" }],
  board: { hypotheses: [], memory: [{ id: "mem_1", kind: "hint", content: "先检查首页", artifact_ids: [], source: "user" }], strategy_cards: [{ id: "card_1", task_id: "task_1", title: "验证已知入口", summary: "候选策略", claims: [], prerequisites: [], target_version_checks: [], status: "testing", active_step_id: "step_1", sources: [{ hint_id: "mem_1", extraction_status: "extracted", source_refs: ["artifact_1#segment-1"] }], steps: [{ id: "step_1", title: "读取目标证据", instructions: "", expected_request: "GET /", success_marker: "flag", failure_conditions: [], risk: "passive", status: "testing", action_ids: ["act_1"], evidence_artifact_ids: ["artifact_1"], last_result: "HTTP 200" }] }] },
  actions: [{ id: "act_1", capability: "http.request", target: "http://target.local", status: "succeeded", strategy_card_id: "card_1", strategy_step_id: "step_1", rationale: "Agent Session tool call", summary: "HTTP 200，发现 Flag", artifact_ids: ["artifact_1"], arguments: { method: "GET" } }],
  artifacts: [{ id: "artifact_1", kind: "http_response", path: "landing.txt", tool: "http.request", target: "http://target.local", excerpt: "Authorization: Bearer should-not-leak" }], flags: [{ value: "flag{evidence_backed}", evidence_artifact_id: "artifact_1" }], findings: [],
  http_sessions: [{ profile: "persistent", origin_count: 1, request_count: 1, rebuild_count: 0, cross_process_recovery: false }],
  observer: { directives: [] },
  context_metrics: [{ turn: 1, audit_message_count: 5, working_message_count: 4, working_chars: 2048, summary_hits: 1, artifact_retrievals: 1 }],
  events: [
    { id: "evt_1", task_id: "task_1", seq: 1, type: "SESSION_STARTED", payload: { runtime: "agent_session" }, created_at: "2026-07-13T00:00:00Z" },
    { id: "evt_2", task_id: "task_1", seq: 2, type: "MESSAGE_START", payload: { role: "assistant" }, created_at: "2026-07-13T00:00:01Z" },
    { id: "evt_3", task_id: "task_1", seq: 3, type: "MESSAGE_END", payload: { content: "我会直接检查目标。" }, created_at: "2026-07-13T00:00:02Z" },
    { id: "evt_4", task_id: "task_1", seq: 4, type: "TOOL_EXECUTION_START", payload: { action_id: "act_1", tool_name: "tga_http_request" }, created_at: "2026-07-13T00:00:03Z" },
    { id: "evt_5", task_id: "task_1", seq: 5, type: "TOOL_EXECUTION_END", payload: { action_id: "act_1", tool_name: "tga_http_request", status: "succeeded", summary: "HTTP 200，发现 Flag", artifacts: [{ artifact_id: "artifact_1" }] }, created_at: "2026-07-13T00:00:04Z" },
    { id: "evt_6", task_id: "task_1", seq: 6, type: "FLAG_FOUND", payload: { value: "flag{evidence_backed}", artifact_id: "artifact_1" }, created_at: "2026-07-13T00:00:05Z" },
  ],
};

async function mockRuntime(page: Page) {
  await page.route("**/api/v2/settings/llm", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true, model: "mock-model" }) }));
  await page.route("**/api/v2/tasks", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ tasks: [{ task_id: "task_1", name: task.name, mode: "ctf", target: task.target, created_at: "2026-07-13T00:00:00Z", status: "running", flags: 1, findings: 0, artifacts: 1 }] }) }));
  await page.route("**/api/v2/tasks/task_1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot) }));
  await page.route("**/api/v2/tasks/task_1/events?*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ events: [], latest_seq: 6 }) }));
  await page.route("**/api/v2/tasks/task_1/events/stream?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {\"latest_seq\":6}\n\n" }));
  await page.route("**/api/v2/tasks/task_1/hints", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ accepted: true }) }));
  await page.route("**/api/v2/tasks/task_1/control", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ accepted: true }) }));
}

test("runtime renders the native Agent Session message and tool loop", async ({ page }) => {
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await expect(page.getByRole("heading", { name: task.name })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Execution timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Target & context" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Session & tools" })).toBeVisible();
  await expect(page.getByTestId("strategy-overview")).toContainText("验证已知入口");
  await expect(page.getByTestId("http-session-overview")).toContainText("persistent");
  await expect(page.getByTestId("context-overview")).toContainText("2,048 chars");
  await expect(page.getByTestId("flow-action")).toContainText("1. GET /");
  await expect(page.getByTestId("flow-action-flag")).toContainText("FLAG FOUND");
  await page.getByRole("button", { name: "Evidence 1" }).click();
  await expect(page.getByRole("dialog", { name: "证据与结果" }).getByText("http.request")).toBeVisible();
  await page.getByRole("button", { name: "关闭证据" }).click();
  await expect(page.getByText("我会直接检查目标。")).toBeVisible();
  await page.getByRole("tab", { name: "Tools" }).click();
  await expect(page.getByText("HTTP 200，发现 Flag")).toBeVisible();
});

test("hint uses only the Runtime API and replay never exposes control actions", async ({ page }) => {
  const targetRequests: string[] = [];
  page.on("request", (request) => { if (request.url().startsWith(task.target)) targetRequests.push(request.url()); });
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await page.getByRole("button", { name: "+ Hint" }).click();
  await page.getByRole("textbox", { name: /补充提示/ }).fill("已知失败边界");
  await page.getByRole("button", { name: "提交提示" }).click();
  await expect(page.getByText("提示已提交，会加入 Solver Session 上下文。")).toBeVisible();
  await page.getByRole("button", { name: "Replay" }).click();
  await expect(page.getByText(/回放模式：只读取已存 AgentEvent/)).toBeVisible();
  await expect(page.getByRole("button", { name: "取消" })).toHaveCount(0);
  expect(targetRequests).toEqual([]);
});

for (const width of [1280, 1024, 768]) {
  test(`runtime remains reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockRuntime(page);
    await page.goto("/tasks/task_1/runtime");
    await expect(page.getByRole("heading", { name: task.name })).toBeVisible();
    if (width <= 1024) await expect(page.getByRole("tab", { name: "Topology" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  });
}
