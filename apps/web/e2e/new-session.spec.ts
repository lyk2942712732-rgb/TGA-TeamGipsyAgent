import { expect, test } from "@playwright/test";

const policy = {
  network: { mode: "none", allowed_scopes: [], rate_limit: 30, concurrency: 2 },
  filesystem: { mode: "read_only", allowed_roots: [] },
  process_execution: { mode: "forbidden", timeout_seconds: 60 },
  fuzzing: { mode: "disabled", max_cases: 0, max_duration_seconds: 0, concurrency: 0 },
  state_change: { mode: "forbidden", allowed_actions: [] },
  containment: { mode: "observe_only", allowed_actions: [] },
  source: "default",
};

test("new task selects a scene and stages task files plus Hint without task-level MCP grants", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") browserErrors.push(message.text()); });
  let upload = 0;
  let createPayload: Record<string, unknown> | undefined;
  await page.route("**/api/v2/settings/llm", (route) => route.fulfill({ json: { configured: true, model: "mock-model" } }));
  await page.route("**/api/v2/mode-profiles", (route) => route.fulfill({ json: {
    schema_version: 3,
    profiles: [{
      id: "ctf", label: "CTF 解题", description: "Solve with evidence", default_goal: "Recover a verified flag.",
      default_mode_config: { mode: "ctf", subtype: "auto", expected_flag_count: 1, verifier: { kind: "local_regex" } },
      default_execution_policy: { ...policy, mcp: { enabled_servers: ["legacy"], enabled_tools: [] } },
      allowed_input_kinds: ["file", "archive", "image"], required_conditions: ["task_files_or_hint"],
      recommended_capabilities: [], prompt_instruction: "", completion_validator: "ctf", report_sections: ["evidence"],
      uses_flag: true, advanced_settings: [], mode_config_schema: {}, execution_policy_schema: {},
    }],
  } }));
  await page.route("**/api/v2/tools/health", (route) => route.fulfill({ json: {
    configured: true,
    records: [
      { server: "fixture", configured: true, enabled: true, discovered: true },
      { server: "disabled", configured: true, enabled: false, discovered: true },
    ],
  } }));
  await page.route("**/api/v2/input-uploads?*", async (route) => {
    upload += 1;
    const name = new URL(route.request().url()).searchParams.get("filename") ?? "file.bin";
    await route.fulfill({ status: 201, json: { asset: {
      id: `asset_${String(upload).padStart(32, "0")}`,
      originalName: name,
      mimeType: name.endsWith(".png") ? "image/png" : "text/plain",
      mediaKind: name.endsWith(".png") ? "image" : "text",
      size: route.request().postDataBuffer()?.byteLength ?? 0,
      sha256: "a".repeat(64), status: "uploaded",
    } } });
  });
  await page.route("**/api/v2/tasks", async (route) => {
    if (route.request().method() === "POST") {
      createPayload = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: { task_id: "task_created", status: "created", scheduled: false, mcp_capabilities: { server_ids: ["fixture"], tools: [] } } });
      return;
    }
    await route.fulfill({ json: { tasks: [] } });
  });
  await page.route("**/api/v2/tasks/task_created/session", (route) => route.fulfill({ json: {
    task: { id: "task_created", name: "new", mode: "ctf", target: "", scope: [] },
    session: { status: "created", turn_count: 0, max_turns: 48 }, solvers: [], challenge: null, subagents: [],
    board: { hypotheses: [], memory: [] }, actions: [], flags: [], findings: [], artifacts: [], events: [], latest_seq: 0,
  } }));
  await page.route("**/api/v2/tasks/task_created/events/stream?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "event: heartbeat\ndata: {}\n\n" }));

  await page.goto("/tasks/new");
  await expect(page.getByRole("heading", { name: "新建任务" })).toBeVisible();
  await expect(page.getByRole("button", { name: /选择场景/ })).toBeVisible();
  await expect(page.getByText("第一步：选择场景")).toBeVisible();
  await expect.poll(async () => ({
    text: await page.locator("body").innerText(),
    errors: browserErrors,
    scripts: await page.locator("script").evaluateAll((items) => items.map((item) => item.getAttribute("src"))),
  }), { timeout: 5000 }).toMatchObject({ text: expect.stringContaining("任务材料与 Hint"), errors: [] });
  await page.getByRole("button", { name: /任务材料与 Hint/ }).click();
  const fileInputs = page.locator('input[type="file"]');
  await fileInputs.nth(0).setInputFiles({ name: "challenge.txt", mimeType: "text/plain", buffer: Buffer.from("question") });
  await fileInputs.nth(1).setInputFiles({ name: "diagram.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a", "hex") });
  await expect(page.getByText("已上传")).toHaveCount(2);
  await expect(page.getByAltText("diagram.png 缩略图")).toBeVisible();
  await page.getByLabel("Hint 文本").fill("Analyze the supplied diagram.");

  await page.getByRole("button", { name: /执行边界/ }).click();
  await expect(page.getByLabel("网络权限")).toBeVisible();
  await expect(page.getByText("MCP 服务与方法授权")).toHaveCount(0);
  await page.getByRole("button", { name: /创建摘要/ }).click();
  await expect(page.getByText("fixture")).toBeVisible();
  await expect(page.getByText("disabled")).toHaveCount(0);
  await page.getByRole("button", { name: "创建任务并开始" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_created\/runtime$/);

  expect(createPayload).toMatchObject({
    mode: "ctf",
    input: {
      taskFileIds: [`asset_${"1".padStart(32, "0")}`],
      hintText: "Analyze the supplied diagram.",
      hintFileIds: [`asset_${"2".padStart(32, "0")}`],
    },
  });
  for (const removed of ["targetUrls", "references", "mcpResources", "mcpTools", "mcpServiceGrants", "mcpMethodGrants", "mcp_servers", "targets"]) {
    expect(createPayload).not.toHaveProperty(removed);
  }
  expect(createPayload?.executionPolicy).not.toHaveProperty("mcp");
});
