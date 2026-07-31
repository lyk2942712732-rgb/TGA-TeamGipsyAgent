import { expect, test } from "@playwright/test";

const policy = {
  preset: "autonomous_ctf",
  network: { access: "public_internet", interaction: "interact", seed_origins: [], custom_origins: [], custom_domains: [], custom_cidrs: [], deny_private_networks: true, deny_loopback: true, deny_link_local: true, deny_cloud_metadata: true, rate_limit_per_minute: 30, concurrency: 2, request_timeout_seconds: 30 },
  local_compute: { mode: "isolated", timeout_seconds: 120, concurrency: 2, network_inheritance: "task_network_policy" },
  high_impact: { mode: "approval_required", allowed_actions: [] },
};

test("new task selects a scene and stages task files plus Hint without task-level MCP grants", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") browserErrors.push(message.text()); });
  let upload = 0;
  let createPayload: Record<string, unknown> | undefined;
  await page.route("**/api/v2/settings/llm", (route) => route.fulfill({ json: { configured: true, model: "mock-model" } }));
  await page.route("**/api/v2/mode-profiles", (route) => route.fulfill({ json: {
    schema_version: 5,
    profiles: [{
      id: "ctf", label: "CTF 解题", description: "Solve with evidence", default_goal: "Recover a verified flag.",
      default_mode_config: { mode: "ctf", subtype: "auto", expected_flag_count: 1, verifier: { kind: "local_regex" } },
      default_execution_policy: policy,
      allowed_input_kinds: ["file", "archive", "image"], required_conditions: ["prompt_or_files"],
      recommended_capabilities: [], completion_validator: "ctf", report_sections: ["evidence"],
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
  await page.route("**/api/v2/tasks/skill-preview", (route) => route.fulfill({ json: {
    selector: "fixture",
    fingerprint: "s".repeat(64),
    count: 0,
    skills: [],
  } }));
  await page.route("**/api/v2/tasks/preflight", (route) => route.fulfill({ json: {
    fingerprint: "f".repeat(64),
    task_id: "task_created",
    checks: [
      { id: "inputs", status: "passed", detail: "Inputs verified" },
      { id: "model", status: "passed", detail: "Model verified" },
    ],
    skill_snapshot: { selector: "fixture", count: 0, content_sha256: "a".repeat(64) },
    mcp_catalog_version: "fixture-v1",
    model_verification_id: "model-verification-fixture",
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
  await page.goto("/tasks/new");
  await expect(page.getByRole("heading", { name: "新建任务" })).toBeVisible();
  await expect(page.getByRole("button", { name: /选择场景/ })).toBeVisible();
  await expect(page.getByText("第一步：选择场景")).toBeVisible();
  await expect.poll(async () => ({
    text: await page.locator("body").innerText(),
    errors: browserErrors,
    scripts: await page.locator("script").evaluateAll((items) => items.map((item) => item.getAttribute("src"))),
  }), { timeout: 5000 }).toMatchObject({ text: expect.stringContaining("任务提示与材料"), errors: [] });
  await page.getByRole("button", { name: /任务提示与材料/ }).click();
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles([
    { name: "challenge.txt", mimeType: "text/plain", buffer: Buffer.from("question") },
    { name: "diagram.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a", "hex") },
  ]);
  await expect(page.getByText("已上传")).toHaveCount(2);
  await expect(page.getByAltText("diagram.png 缩略图")).toBeVisible();
  await page.getByLabel("任务提示词").fill("Analyze the supplied diagram.");

  await page.getByRole("button", { name: /执行边界/ }).click();
  await expect(page.getByLabel("网络访问")).toBeVisible();
  await expect(page.getByText("MCP 服务与方法授权")).toHaveCount(0);
  await page.getByRole("button", { name: /创建摘要/ }).click();
  await expect(page.getByText("fixture")).toBeVisible();
  await expect(page.getByText("disabled")).toHaveCount(0);
  await page.getByRole("button", { name: "创建任务并开始" }).click();
  await expect(page).toHaveURL(/\/tasks\/task_created\/runtime$/);

  expect(createPayload).toMatchObject({
    mode: "ctf",
    preflightFingerprint: "f".repeat(64),
    input: {
      text: "Analyze the supplied diagram.",
    },
  });
  const createdInput = createPayload?.input as { text: string; fileIds: string[] };
  expect(createdInput.fileIds.sort()).toEqual([
    `asset_${"1".padStart(32, "0")}`,
    `asset_${"2".padStart(32, "0")}`,
  ]);
  expect(createdInput.fileIds).toHaveLength(2);
  for (const removed of ["targetUrls", "references", "mcpResources", "mcpTools", "mcpServiceGrants", "mcpMethodGrants", "mcp_servers", "targets"]) {
    expect(createPayload).not.toHaveProperty(removed);
  }
  expect(createPayload?.executionPolicy).not.toHaveProperty("mcp");
});
