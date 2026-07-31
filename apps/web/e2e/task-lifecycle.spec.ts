import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const artifactRoot = process.env.TGA_STAGE_B_ARTIFACTS ?? join(process.cwd(), "test-results", "stage-b-artifacts");

const task = {
  task_id: "task-alpha", name: "Alpha task", mode: "ctf", task_entry_url: null,
  target_summary: "challenge.txt", target_count: 1, hint_count: 1,
  created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:02:00Z",
  status: "awaiting_approval", turn_count: 2, max_turns: 20, active_solvers: 1,
  pending_approvals: 1, needs_attention: true, intent_total: 3, intent_completed: 1,
  latest_event: { seq: 4, type: "APPROVAL_REQUESTED" }, flags: 0, findings: 1, artifacts: 2,
};

const detail = {
  schema_version: 6, task_id: "task-alpha",
  task: { id: "task-alpha", name: "Alpha task", mode: "ctf", goal: "Recover verified evidence", task_entry_url: null, schema_version: 6 },
  task_spec: { task_id: "task-alpha", objective: "Recover verified evidence", instructions: [{ id: "instruction-1", content: "Inspect the supplied input" }], constraints: [], success_criteria: [{ id: "success-1", content: "A confirmed result exists" }], resources: [] },
  lifecycle: { created_at: "2026-07-30T00:00:00Z", updated_at: "2026-07-30T00:02:00Z", status: "awaiting_approval", turn_count: 2, max_turns: 20, active_solvers: 1, pending_approvals: 1, intent_total: 3, intent_completed: 1, flags: 0, findings: 1, artifacts: 2, needs_attention: true, latest_event: { seq: 4, type: "APPROVAL_REQUESTED", created_at: "2026-07-30T00:02:00Z" } },
  input_summary: { prompt_present: true, prompt_preview: "Inspect the supplied input", file_count: 1, files: [], task_entry_url: null },
  config_snapshot: { mode_config: { mode: "ctf" }, execution_policy: { preset: "autonomous_ctf", network: { access: "public_internet" }, high_impact: { mode: "approval_required" } }, execution_budget: {}, model: null, mcp_capabilities: {}, task_common_skills: null, agent_prompt: null },
};

async function mockTaskPages(page: Page) {
  await page.route("**/api/v2/tasks?*", async (route) => {
    if (route.request().method() === "GET") await route.fulfill({ json: { tasks: [task], offset: 0, limit: 100, total: 1, next_offset: null } });
  });
  await page.route("**/api/v2/tasks/task-alpha", (route) => route.fulfill({ json: detail }));
  await page.route("**/api/v2/tasks/task-alpha/team", (route) => route.fulfill({ json: { task_id: "task-alpha", team: { status: "running", supervisor_solver_id: "supervisor", active_solver_count: 1, max_active_workers: 2 }, solvers: [] } }));
  await page.route("**/api/v2/tasks/task-alpha/inputs", (route) => route.fulfill({ json: { task_goal: detail.task.goal, prompt: "Inspect the supplied input", files: [{ id: "input-1", label: "challenge.txt", mime_type: "text/plain", size: 12 }] } }));
  await page.route("**/api/v2/tasks/task-alpha/evidence?*", (route) => route.fulfill({ json: { task_id: "task-alpha", artifacts: { offset: 0, limit: 100, total: 0, items: [] }, evidence_claims: { offset: 0, limit: 100, total: 0, items: [] }, findings: { offset: 0, limit: 100, total: 0, items: [] } } }));
  await page.route("**/api/v2/tasks/task-alpha/timeline?*", (route) => route.fulfill({ json: { task_id: "task-alpha", after_seq: 0, next_after_seq: 1, latest_seq: 1, has_more: false, events: [{ schema_version: 6, id: "event-1", task_id: "task-alpha", seq: 1, type: "TASK_INPUT_ANALYZED", payload: { summary: "input analyzed" }, created_at: "2026-07-30T00:01:00Z" }] } }));
}

async function mockCreatePage(page: Page) {
  await page.route("**/api/v2/mode-profiles", (route) => route.fulfill({ json: { schema_version: 5, profiles: [] } }));
  await page.route("**/api/v2/tools/health", (route) => route.fulfill({ json: { configured: true, records: [] } }));
}

const viewports = [
  { width: 390, height: 844 },
  { width: 1024, height: 768 },
  { width: 1280, height: 900 },
  { width: 1440, height: 960 },
  { width: 1448, height: 1086 },
  { width: 1920, height: 1080 },
];

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

for (const viewport of viewports) {
  test(`task lifecycle pages fit ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockTaskPages(page);
    await mockCreatePage(page);
    await mkdir(artifactRoot, { recursive: true });

    const requests: string[] = [];
    page.on("request", (request) => { if (request.url().includes("/api/v2/tasks/")) requests.push(new URL(request.url()).pathname); });

    await page.goto("/tasks");
    await expect(page.getByRole("heading", { name: "任务" })).toBeVisible();
    await expect(page.getByRole("table", { name: "任务列表" })).toBeVisible();
    await page.getByLabel("处理状态筛选").selectOption("true");
    await expect(page).toHaveURL(/needs_attention=true/);
    await page.getByRole("button", { name: "卡片视图" }).click();
    await expect(page.getByRole("heading", { name: "Alpha task" })).toBeVisible();
    expect(requests.some((path) => path.endsWith("/session"))).toBe(false);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: join(artifactRoot, `tasks-${viewport.width}.png`), fullPage: true });

    await page.goto("/tasks/task-alpha");
    await expect(page.getByRole("heading", { name: "Alpha task" })).toBeVisible();
    expect(requests.filter((path) => path.endsWith("/task-alpha")).length).toBeGreaterThanOrEqual(1);
    expect(requests.some((path) => path.endsWith("/task-alpha/session"))).toBe(false);
    await page.getByRole("tab", { name: /团队/ }).click();
    await expect(page.getByRole("heading", { name: "团队摘要" })).toBeVisible();
    await page.getByRole("tab", { name: /输入/ }).click();
    await expect(page.getByRole("heading", { name: "输入摘要" })).toBeVisible();
    await page.getByRole("tab", { name: /结果/ }).click();
    await expect(page.getByRole("heading", { name: "已确认结果" })).toBeVisible();
    await page.getByRole("tab", { name: /历史/ }).click();
    await expect(page.getByRole("heading", { name: "持久化事件历史" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: join(artifactRoot, `task-detail-${viewport.width}.png`), fullPage: true });

    await page.goto("/tasks/new");
    await expect(page.getByRole("heading", { name: "创建任务 · 五步向导" })).toBeVisible();
    await expect(page.getByRole("button", { name: "任务目标" })).toBeVisible();
    await expect(page.getByRole("button", { name: /启动前检查/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: join(artifactRoot, `task-create-${viewport.width}.png`), fullPage: true });
  });
}
