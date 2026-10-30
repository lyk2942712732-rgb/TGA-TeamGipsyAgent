import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: "workbench-visual.spec.ts",
  testIgnore: [],
  timeout: 90000,
  use: { ...base.use, baseURL: "http://127.0.0.1:5175", viewport: { width: 1440, height: 1000 } },
  webServer: [{ command: `${process.env.TGA_PLAYWRIGHT_PYTHON ?? "python"} e2e/workbench_fixture.py`, url: "http://127.0.0.1:18084/api/v3/health", reuseExistingServer: true }, { command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5175", url: "http://127.0.0.1:5175", env: { TGA_API_PROXY_TARGET: "http://127.0.0.1:18084" }, reuseExistingServer: true }],
});
