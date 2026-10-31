import { expect, test } from "@playwright/test";

test("all workbench pages, native task interactions, saved Solver settings and desktop layouts", async ({ page }, testInfo) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const task = "/tasks/00000000-0000-4000-8000-000000001042/runtime";
  const pages = [
    ["home", "/", "工作台"], ["tasks", "/tasks", "任务"], ["new", "/tasks/new", "创建任务"],
    ["runtime", task, "旧城登录页（联调示例）"], ["attention", "/approvals", "审批中心"],
    ["reports", "/reports", "报告"], ["solvers", "/settings/solvers", "Solver 配置"],
    ["skills", "/settings/skills", "Skills"], ["models", "/settings/models", "模型供应商"],
    ["scenes", "/settings/policies", "场景"], ["system", "/system", "系统状态"], ["notfound", "/missing", "此入口不存在"],
  ];
  for (const [name, path, heading] of pages) {
    await page.goto(path); await expect(page.getByRole("heading", { name: heading, exact: true, level: 1 })).toBeVisible();
    await expect(page.locator(".loading-skeleton")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), name).toBe(true);
  }
  await page.goto(task);
  await expect(page.getByRole("region", { name: "实时运行图" })).toBeVisible();
  await page.getByRole("button", { name: "查看 Claude Worker", exact: true }).click();
  await expect(page.locator(".workbench-inspector").getByRole("heading", { name: "Claude Worker", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "模型信息" }).check();
  await expect(page.locator(".collab-model")).toHaveCount(4);
  await page.getByRole("button", { name: "收起运行图" }).click(); await expect(page.locator(".collab-stage")).toHaveCount(0);
  await page.getByRole("button", { name: "展开运行图" }).click();
  await page.getByRole("checkbox", { name: "模型信息" }).uncheck();
  await page.getByRole("button", { name: "关闭节点详情" }).click();
  expect((await page.request.post("http://127.0.0.1:18084/fixture/activity")).ok()).toBe(true);
  await expect(page.locator(".collab-now")).toContainText("读取黑板");
  await expect(page.locator(".collab-now")).toContainText("读取 Skill");
  await expect(page.locator(".collab-route")).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("runtime-interactions.png"), fullPage: true });
  await page.goto("/settings/solvers");
  const panel = page.getByLabel("OpenAI Worker Solver 配置", { exact: true });
  const interval = panel.getByRole("spinbutton", { name: "黑板同步间隔（秒）" });
  const previous = await interval.inputValue(); await interval.fill("75");
  await panel.getByRole("button", { name: "保存配置" }).click(); await expect(page.locator(".settings-message")).toContainText("已保存");
  await page.reload(); await expect(interval).toHaveValue("75");
  await interval.fill(previous); await panel.getByRole("button", { name: "保存配置" }).click();
  await expect(page.locator(".settings-message")).toContainText("已保存");
  await page.getByRole("tab", { name: "系统提示词" }).click(); await expect(page.getByLabel("OpenAI Worker System Prompt")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("solver-prompt.png"), fullPage: true });
  await page.goto("/settings/models"); await page.getByRole("tab", { name: "Anthropic Messages", exact: true }).click();
  await expect(page.getByRole("region", { name: "Anthropic Messages 模型" })).toBeVisible();
  await page.getByRole("checkbox", { name: "仅看已启用" }).check(); await expect(page.getByRole("switch")).toHaveCount(2);
  await page.goto("/settings/skills"); await page.getByRole("tab", { name: "阅读预览" }).click();
  await expect(page.locator(".workbench-markdown").getByRole("heading", { name: "ctf-common", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("skill-reading.png"), fullPage: true });
  await page.goto("/reports"); await page.getByRole("button", { name: "镜中密文（联调示例） Writeup", exact: true }).click();
  await expect(page.locator(".workbench-report").getByRole("heading", { name: "镜中密文：解题报告" })).toBeVisible();
  await expect(page.locator(".workbench-report").getByRole("table")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("report-reading.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("token totals expand inline and realtime prompts persist on the shared blackboard", async ({ page }, testInfo) => {
  await page.goto("/tasks/00000000-0000-4000-8000-000000001042/runtime");
  const toggle = page.getByRole("button", { name: "展开各智能体 Token 用量" });
  await expect(toggle).toContainText("4,900");
  await toggle.click();
  const breakdown = page.getByRole("group", { name: "各智能体累计 Token" });
  await expect(breakdown).toContainText("1,200");
  await expect(breakdown).toContainText("2,400");
  await expect(breakdown).toContainText("1,300");
  await expect(breakdown).toContainText("未上报");
  await page.getByRole("textbox", { name: "实时提示" }).fill("联调提示：先复用 Scout 材料，再验证入口。");
  await page.getByRole("button", { name: "写入黑板" }).click();
  await expect(page.locator(".tga3-board-card").filter({ hasText: "联调提示：先复用 Scout 材料，再验证入口。" })).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("tokens-and-realtime-prompt.png"), fullPage: true });
  await page.reload();
  await expect(page.locator(".tga3-board-card").filter({ hasText: "联调提示：先复用 Scout 材料，再验证入口。" })).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await toggle.click();
  await expect(breakdown).toBeVisible();
  expect(await page.locator(".tga3-token-usage").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  expect(await page.locator(".tga3-realtime-prompt").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("tokens-mobile.png"), fullPage: true });
});
