import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { join } from "node:path";

const downloadedChrome = process.env.TGA_PLAYWRIGHT_CHROME ?? join(process.env.LOCALAPPDATA ?? "", "ms-playwright", "chromium-1228", "chrome-win64", "chrome.exe");
const port = Number(process.env.TGA_PLAYWRIGHT_PORT ?? 5174);
const baseURL = `http://127.0.0.1:${port}`;
const externalServer = process.env.TGA_PLAYWRIGHT_EXTERNAL_SERVER === "1";
const apiPort = Number(process.env.TGA_PLAYWRIGHT_API_PORT ?? 18083);

export default defineConfig({
  testDir: "./e2e",
  expect: { timeout: 15000 },
  use: { baseURL, headless: true, launchOptions: existsSync(downloadedChrome) ? { executablePath: downloadedChrome } : {} },
  webServer: externalServer ? undefined : [{
    command: `${process.env.TGA_PLAYWRIGHT_PYTHON ?? "python"} e2e/provider_fixture.py`,
    url: `http://127.0.0.1:${apiPort}/api/v3/health`,
    env: { TGA_PLAYWRIGHT_API_PORT: String(apiPort) },
    reuseExistingServer: false,
  }, {
    command: `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    env: { TGA_API_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
  }],
});
