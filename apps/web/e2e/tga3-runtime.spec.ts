import { expect, test } from "@playwright/test";

const taskId = "11111111-1111-4111-8111-111111111111";
const task = { id: taskId, title: "TGA3 fixture", scene_id: "penetration_test", state: "running", blackboard_seq: 1, dialogue_seq: 0, final_snapshot_seq: null, created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:01:00Z" };
const configBundle = {
  models: { schema_version: 1, providers: [
    { id: "openai", name: "OpenAI", protocol: "openai_responses", base_url: "https://api.openai.com/v1", api_keys: [{ id: "primary", label: "Primary", api_key: "sk-editable" }], selected_api_key_id: "primary", models: [{ id: "openai-worker-model", name: "gpt-test", max_output_tokens: 8192, timeout_seconds: 180 }] },
    { id: "anthropic", name: "Anthropic", protocol: "anthropic", base_url: "https://api.anthropic.com", api_keys: [{ id: "primary", label: "Primary", api_key: "claude-editable" }], selected_api_key_id: "primary", models: [{ id: "claude-worker-model", name: "claude-test", max_output_tokens: 8192, timeout_seconds: 180 }] },
  ] },
  agents: { schema_version: 1, agents: {
    supervisor: { display_name: "Supervisor", role: "supervisor", runtime: "openai_agents", provider_id: "openai", model_id: "openai-worker-model", max_turns_per_cycle: 3, system_prompt: "supervisor" },
    "worker-openai": { display_name: "OpenAI Worker", role: "worker", runtime: "openai_agents", provider_id: "openai", model_id: "openai-worker-model", max_turns_per_cycle: 3, system_prompt: "openai worker" },
    "worker-claude": { display_name: "Claude Worker", role: "worker", runtime: "claude_agent", provider_id: "anthropic", model_id: "claude-worker-model", max_turns_per_cycle: 3, system_prompt: "claude worker" },
    reporter: { display_name: "Reporter", role: "reporter", runtime: "openai_agents", provider_id: "openai", model_id: "openai-worker-model", max_turns_per_cycle: 2, system_prompt: "reporter" },
  } },
  scenes: { schema_version: 1, scenes: [{ id: "penetration_test", name: "渗透测试", description: "Web 与网络服务题", system_prompt: "找到 flag" }] },
  runtime: { schema_version: 1, postgres_dsn: "postgresql://local", listen_port: 8083 },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v3/tasks", (route) => route.fulfill({ json: [task] }));
  await page.route(`**/api/v3/tasks/${taskId}`, (route) => route.fulfill({ json: { task, agents: [
    { task_id: taskId, agent_id: "supervisor", display_name: "Supervisor", role: "supervisor", runtime_location: "host", sdk: "openai_agents", desired_state: "idle", actual_state: "idle", provider_id: "openai", model_id: "openai-worker-model", container_id: null, session_id: null, last_error: null, updated_at: task.updated_at },
    { task_id: taskId, agent_id: "worker-openai", display_name: "OpenAI Worker", role: "worker", runtime_location: "container", sdk: "openai_agents", desired_state: "running", actual_state: "running", provider_id: "openai", model_id: "openai-worker-model", container_id: "abc", session_id: "one", last_error: null, updated_at: task.updated_at },
    { task_id: taskId, agent_id: "worker-claude", display_name: "Claude Worker", role: "worker", runtime_location: "container", sdk: "claude_agent", desired_state: "running", actual_state: "running", provider_id: "anthropic", model_id: "claude-worker-model", container_id: "def", session_id: "two", last_error: null, updated_at: task.updated_at },
    { task_id: taskId, agent_id: "reporter", display_name: "Reporter", role: "reporter", runtime_location: "host", sdk: "openai_agents", desired_state: "created", actual_state: "created", provider_id: "openai", model_id: "openai-worker-model", container_id: null, session_id: null, last_error: null, updated_at: task.updated_at },
  ] } }));
  await page.route(`**/api/v3/tasks/${taskId}/blackboard`, (route) => route.fulfill({ json: { published: null, latest_seq: 1, entries: [] } }));
  await page.route(`**/api/v3/tasks/${taskId}/dialogue`, (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v3/tasks/${taskId}/dialogue/stream*`, (route) => route.fulfill({
    body: "\n",
    contentType: "text/event-stream",
  }));
  await page.route("**/api/v3/models", (route) => route.fulfill({ json: { providers: [], bindings: {
    "worker-openai": { display_name: "OpenAI Worker", role: "worker", runtime: "openai_agents", provider_id: "openai", model_id: "openai-worker-model", max_turns_per_cycle: 3 },
  } } }));
  await page.route("**/api/v3/scenes", (route) => route.fulfill({ json: [
    { id: "penetration_test", name: "渗透测试", description: "Web 与网络服务题" },
    { id: "pwn", name: "Pwn", description: "二进制利用题" },
  ] }));
  await page.route("**/api/v3/config", (route) => route.fulfill({ json: route.request().method() === "PUT" ? route.request().postDataJSON() : configBundle }));
  await page.route("**/api/v3/skills", (route) => route.fulfill({ json: [] }));
});

test("new task opens a config-backed scene modal", async ({ page }) => {
  await page.goto("/tasks/new");
  await expect(page.getByRole("dialog", { name: "创建任务" })).toBeVisible();
  await expect(page.getByRole("button", { name: /渗透测试/ })).toBeVisible();
  await expect(page.getByText("openai/openai-worker-model")).toBeVisible();
  await expect(page.getByText("共享黑板")).toBeVisible();
});

test("runtime exposes TGA3 agents, dialogue, blackboard and skills", async ({ page }) => {
  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByRole("button", { name: /OpenAI Worker/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Claude Worker/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "对话" })).toBeVisible();
  await expect(page.getByRole("button", { name: "黑板" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Skills" })).toBeVisible();
  await expect(page.getByText("状态来自后端 agent_runs")).toBeVisible();
});

test("config center reads and writes providers from config", async ({ page }) => {
  await page.goto("/config");
  await expect(page.getByRole("heading", { name: "统一配置中心" })).toBeVisible();
  const providerName = page.getByLabel("显示名称").first();
  await expect(providerName).toHaveValue("OpenAI");
  await providerName.fill("OpenAI Custom");
  const requestPromise = page.waitForRequest((request) => request.url().endsWith("/api/v3/config") && request.method() === "PUT");
  await page.getByRole("button", { name: "保存全部配置" }).click();
  const request = await requestPromise;
  expect(request.postDataJSON().models.providers[0].name).toBe("OpenAI Custom");
  await expect(page.getByText(/配置已校验并原子写回/)).toBeVisible();
});
