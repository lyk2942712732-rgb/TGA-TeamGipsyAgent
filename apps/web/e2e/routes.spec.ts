import { expect, test } from "@playwright/test";

test("product shell routes do not trigger task or model queries", async ({ page }) => {
  const productRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v2\/(tasks|settings\/llm)/.test(request.url())) productRequests.push(request.url());
  });

  await page.goto("/resources");
  await expect(page.getByRole("heading", { name: "资源" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("button")).toHaveCount(13);
  expect(productRequests).toEqual([]);
});

test("removed Settings and Session aliases return a non-compatible result", async ({ page }) => {
  await page.goto("/settings/capabilities");
  await expect(page.getByRole("heading", { name: "此入口不存在" })).toBeVisible();
  await expect(page).toHaveURL(/\/settings\/capabilities$/);

  await page.goto("/sessions/legacy%20task/replay?tab=evidence");
  await expect(page.getByRole("heading", { name: "此入口不存在" })).toBeVisible();
  await expect(page).toHaveURL(/\/sessions\/legacy%20task\/replay\?tab=evidence$/);
});
