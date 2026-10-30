# TGA desktop workbench

The frontend uses a shared neutral theme, compact desktop layouts, and a native task graph. The graph reads task events, blackboard entries, and acknowledged per-Agent blackboard cursors; historical events are not replayed as live activity.

```powershell
npm ci
npm test
npm run build
```

Run the provider and Solver regression with `npm run test:e2e`. Run the full-page workbench verification and screenshots with `npm run test:workbench`. Set `TGA_PLAYWRIGHT_PYTHON` to a Python interpreter with the TGA3 dependencies installed. Install Chromium with `npx playwright install chromium`, or set `TGA_PLAYWRIGHT_CHROME` to its executable.

The workbench test starts a separate API on port 18084 and Vite on port 5175. Its temporary configuration, storage, example tasks, Skills, and Markdown report are isolated from the project configuration and production database. It does not run models or containers. Screenshots are saved under `test-results`.

The standard development server uses `npm run dev` and proxies `/api` to `TGA_API_PROXY_TARGET` (default `http://127.0.0.1:8083`).
