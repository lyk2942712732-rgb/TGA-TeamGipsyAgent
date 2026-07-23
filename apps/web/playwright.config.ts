import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { join } from "node:path";

const downloadedChrome = join(process.env.LOCALAPPDATA ?? "", "ms-playwright", "chromium-1228", "chrome-win64", "chrome.exe");
const port = Number(process.env.TGA_PLAYWRIGHT_PORT ?? 5174);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL, headless: true, launchOptions: existsSync(downloadedChrome) ? { executablePath: downloadedChrome } : {} },
  webServer: { command: `npm run dev -- --port ${port}`, url: baseURL, reuseExistingServer: false },
});
