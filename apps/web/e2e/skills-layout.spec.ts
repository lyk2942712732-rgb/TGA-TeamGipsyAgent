import { expect, test } from "@playwright/test";

const documents = [
  { path: "SKILL.md", title: "CTF Web", size: 1024, sha256: "a".repeat(64) },
  ...Array.from({ length: 20 }, (_, index) => ({
    path: `reference-${index + 1}.md`,
    title: `Web reference ${index + 1}`,
    size: 16_000 + index,
    sha256: "b".repeat(64),
  })),
];

const skill = {
  name: "ctf-web",
  tags: ["ctf", "web", "sqli", "ssrf", "authentication"],
  version: "1",
  summary: "Provides web exploitation techniques for CTF challenges without forcing a long description into a narrow table column.",
  entrypoint: "SKILL.md" as const,
  file_count: documents.length,
  total_bytes: documents.reduce((sum, item) => sum + item.size, 0),
  content_sha256: "c".repeat(64),
  enabled: true,
  instructions: "# CTF Web\n\nUse the references that match the observed target.",
  documents,
};

test("Skills keeps one page scroller and readable master-detail columns", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 640 });
  await page.route("**/api/v2/dashboard", (route) => route.fulfill({ json: {
    view_version: 1,
    generated_at: new Date(0).toISOString(),
    metrics: { running_tasks: 0, pending_approvals: 0, active_solvers: 0 },
    needs_attention: [], active_tasks: [], recent_completed: [], system_status: [], unavailable_metrics: [],
  } }));
  await page.route("**/api/v2/settings/skills**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v2/settings/skills") {
      return route.fulfill({ json: { schema_version: 2, root: "runs2/.config/skills", skills: [skill] } });
    }
    return route.fulfill({ json: { skill } });
  });

  await page.goto("/settings/skills");
  await expect(page.getByRole("heading", { name: "Skills 管理" })).toBeVisible();
  await expect(page.getByRole("button", { name: /ctf-web/ })).toBeVisible();

  const main = page.locator(".app-main");
  await expect.poll(() => main.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }))).toMatchObject({ overflowY: "auto" });
  expect(await main.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await main.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  expect(await main.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  const card = await page.getByRole("button", { name: /ctf-web/ }).boundingBox();
  expect(card?.width ?? 0).toBeGreaterThan(260);
  expect(card?.height ?? 999).toBeLessThan(190);
  await expect(page.getByLabel("Skill 文档")).toBeVisible();
});
