import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const artifactRoot = process.env.TGA_STAGE_B_ARTIFACTS ?? join(process.cwd(), "test-results", "stage-b-artifacts");

test("approval center matches the 1448px reference layout", async ({ page }) => {
  await page.setViewportSize({ width: 1448, height: 1086 });
  await page.route("**/api/v2/approvals?*", (route) => route.fulfill({
    json: { schema_version: 1, offset: 0, limit: 12, total: 0, next_offset: null, items: [], filters: {} },
  }));
  await mkdir(artifactRoot, { recursive: true });

  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "审批中心" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "待处理（3）" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("article")).toHaveCount(3);
  await expect(page.getByRole("heading", { name: "审批服务器文件" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "发起 SQL 注入测试" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "上传测试文件" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: join(artifactRoot, "approvals-1448.png"), fullPage: true });

  const first = page.getByRole("article").filter({ hasText: "审批服务器文件" });
  await first.getByRole("button", { name: "批准一次" }).click();
  await page.getByRole("dialog", { name: "批准本次操作？" }).getByRole("button", { name: "批准一次" }).click();
  await expect(page.getByText("已提交一次性批准")).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(2);
});
