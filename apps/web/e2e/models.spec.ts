import { expect, test, type Page } from "@playwright/test";


async function mockModels(page: Page, capture: (payload: Record<string, unknown>) => void) {
  await page.route("**/api/v2/tasks", (route) => route.fulfill({ json: { tasks: [] } }));
  await page.route("**/api/v2/settings/llm", async (route) => {
    if (route.request().method() === "POST") {
      capture(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: {
        configured: true,
        base_url: "https://provider.example/v1",
        model: "tool-model",
        api_key_set: true,
        browser_configured: true,
        supports_vision: true,
      } });
      return;
    }
    await route.fulfill({ json: {
      configured: true,
      base_url: "https://provider.example/v1",
      model: "existing-model",
      api_key_set: true,
      browser_configured: true,
      supports_vision: false,
    } });
  });
}


for (const viewport of [{ width: 1280, height: 900 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`browser configures a write-only model credential at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let payload: Record<string, unknown> | undefined;
    await mockModels(page, (value) => { payload = value; });

    await page.goto("/");
    await page.getByTitle("Provider 与模型").click();
    await expect(page).toHaveURL(/\/settings\/models$/);
    await expect(page.getByRole("heading", { name: "Models" })).toBeVisible();
    await page.getByRole("button", { name: "配置", exact: true }).click();
    const key = page.getByRole("textbox", { name: "API Key", exact: true });
    await expect(key).toHaveAttribute("type", "password");
    await expect(key).toHaveValue("");
    await expect(key).toHaveAttribute("placeholder", "已保存，留空表示不修改");
    await key.fill("browser-secret");
    await page.getByRole("button", { name: "显示 API Key" }).click();
    await expect(key).toHaveAttribute("type", "text");
    await page.getByLabel("模型 ID").fill("tool-model");
    await page.getByLabel("视觉输入").selectOption("true");
    await page.getByRole("button", { name: "保存设置" }).click();

    await expect(page.getByText(/API Key 不会回显/)).toBeVisible();
    await page.getByRole("button", { name: "配置", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "API Key", exact: true })).toHaveValue("");
    expect(payload).toMatchObject({
      base_url: "https://provider.example/v1",
      model: "tool-model",
      api_key: "browser-secret",
      supports_vision: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
