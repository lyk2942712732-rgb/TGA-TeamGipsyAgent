import { expect, test } from "@playwright/test";

const taskId = "11111111-1111-4111-8111-111111111111";
const task = { id: taskId, title: "TGA3 fixture", state: "running", blackboard_seq: 1, dialogue_seq: 0, final_snapshot_seq: null, created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:01:00Z" };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v3/tasks", (route) => route.fulfill({ json: [task] }));
  await page.route(`**/api/v3/tasks/${taskId}`, (route) => route.fulfill({ json: { task, agents: [
    { task_id: taskId, agent_id: "worker-openai", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "openai-worker-model", container_id: "abc", session_id: "one", last_error: null, updated_at: task.updated_at },
    { task_id: taskId, agent_id: "worker-claude", sdk: "claude_agent", desired_state: "running", actual_state: "running", provider_id: "anthropic", model_id: "claude-worker-model", container_id: "def", session_id: "two", last_error: null, updated_at: task.updated_at },
  ] } }));
  await page.route(`**/api/v3/tasks/${taskId}/blackboard`, (route) => route.fulfill({ json: { published: null, latest_seq: 1, entries: [] } }));
  await page.route(`**/api/v3/tasks/${taskId}/dialogue`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/tasks/${taskId}/dialogue/stream*`, (route) => route.fulfill({
    body: "\n",
    contentType: "text/event-stream",
  }));
  await page.route("**/api/v3/models", (route) => route.fulfill({ json: { providers: [], bindings: {} } }));
  await page.route("**/api/v3/skills", (route) => route.fulfill({ json: [] }));
});

test("runtime exposes TGA3 agents, dialogue, blackboard and skills", async ({ page }) => {
  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByRole("button", { name: /OpenAI Worker/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Claude Worker/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "对话" })).toBeVisible();
  await expect(page.getByRole("button", { name: "黑板" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Skills" })).toBeVisible();
  await expect(page.getByText("当前 Intent")).toHaveCount(0);
  await expect(page.getByText("Reviewer")).toHaveCount(0);
});
