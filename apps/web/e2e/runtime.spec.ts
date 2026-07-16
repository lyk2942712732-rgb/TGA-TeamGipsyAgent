import { expect, test, type Page } from "@playwright/test";

const task = { id: "task_1", name: "本地 Web CTF", mode: "ctf", target: "http://target.local", scope: ["target.local"] };
const snapshot = {
  task, latest_seq: 5,
  session: { status: "running", turn_count: 1, max_turns: 48 }, solvers: [{ id: "solver_1", role: "main", status: "running" }],
  board: { hypotheses: [{ id: "hyp_1", statement: "入口可能暴露交互契约", attack_class: "recon", entry_point: "/", rationale: "尚未建立端点清单", next_test: "请求首页", status: "testing", confidence: 0.8, attempt_count: 1, evidence_artifact_ids: ["artifact_1"], last_result: "等待验证" }], memory: [] },
  actions: [{ id: "act_1", capability: "http.request", target: "http://target.local", status: "succeeded", hypothesis_id: "hyp_1", rationale: "请求首页", summary: "HTTP 200，发现表单", artifact_ids: ["artifact_1"] }],
  artifacts: [{ id: "artifact_1", kind: "http_response", path: "landing.txt", tool: "http.request", target: "http://target.local", excerpt: "Authorization: Bearer should-not-leak" }], flags: [{ value: "flag{evidence_backed}", evidence_artifact_id: "artifact_1" }], findings: [],
  events: [
    { id: "evt_1", task_id: "task_1", seq: 1, type: "HYPOTHESIS_CREATED", payload: { hypothesis_id: "hyp_1", statement: "入口可能暴露交互契约" }, created_at: "2026-07-13T00:00:00Z" },
    { id: "evt_2", task_id: "task_1", seq: 2, type: "ACTION_PROPOSED", payload: { action_id: "act_1", capability: "http.request", target: "http://target.local" }, created_at: "2026-07-13T00:00:01Z" },
    { id: "evt_3", task_id: "task_1", seq: 3, type: "ACTION_STARTED", payload: { action_id: "act_1" }, created_at: "2026-07-13T00:00:02Z" },
    { id: "evt_4", task_id: "task_1", seq: 4, type: "ACTION_FINISHED", payload: { action_id: "act_1", status: "succeeded", summary: "HTTP 200，发现表单", artifact_ids: ["artifact_1"] }, created_at: "2026-07-13T00:00:03Z" },
    { id: "evt_5", task_id: "task_1", seq: 5, type: "GATE_REJECTED", payload: { kind: "flag", reason: "flag_format_or_provenance_failed" }, created_at: "2026-07-13T00:00:04Z" },
  ],
};

async function mockRuntime(page: Page) {
  await page.route("**/api/v2/tasks", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ tasks: [{ task_id: "task_1", name: task.name, mode: "ctf", target: task.target, created_at: "2026-07-13T00:00:00Z", status: "running", flags: 1, findings: 0, artifacts: 1 }] }) }));
  await page.route("**/api/v2/tasks/task_1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot) }));
  await page.route("**/api/v2/tasks/task_1/events?*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ events: [], latest_seq: 5 }) }));
  await page.route("**/api/v2/tasks/task_1/events/stream?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {\"latest_seq\":5}\n\n" }));
  await page.route("**/api/v2/tasks/task_1/hints", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ accepted: true }) }));
  await page.route("**/api/v2/tasks/task_1/control", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ accepted: true }) }));
}

test("runtime distinguishes planned, executed and policy-rejected states", async ({ page }) => {
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await expect(page.getByRole("heading", { name: task.name })).toBeVisible();
  await expect(page.getByText("已计划，未执行", { exact: false })).toBeVisible();
  await expect(page.getByText("HTTP 200，发现表单")).toBeVisible();
  await page.getByRole("tab", { name: "Safety" }).click();
  await expect(page.getByTestId("evidence").getByText("flag_format_or_provenance_failed")).toBeVisible();
});

test("hint uses only the Runtime API and replay never exposes control actions", async ({ page }) => {
  const targetRequests: string[] = [];
  page.on("request", (request) => { if (request.url().startsWith(task.target)) targetRequests.push(request.url()); });
  await mockRuntime(page);
  await page.goto("/tasks/task_1/runtime");
  await page.getByRole("textbox", { name: /补充提示/ }).fill("已知失败边界");
  await page.getByRole("button", { name: "提交提示" }).click();
  await expect(page.getByText("提示已提交，等待策略记忆事件吸收。")).toBeVisible();
  await page.getByRole("button", { name: "回放" }).click();
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
    if (width <= 1024) await expect(page.getByRole("tab", { name: "证据与结果" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
