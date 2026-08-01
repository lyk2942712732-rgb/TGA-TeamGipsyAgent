# TGA Runbook

## Install

```bash
python -m pip install -e ".[dev]"
cd apps/web
npm install
```

## Configure a model

TGA uses a generic OpenAI-compatible tool-calling endpoint. Set these values in
the process environment, not in task files or source control:

```text
TGA_LLM_API_KEY
TGA_LLM_BASE_URL
TGA_LLM_MODEL
```

Optional limits include `TGA_LLM_TIMEOUT_S`, `TGA_LLM_MAX_OUTPUT_TOKENS`,
`TGA_LLM_TEMPERATURE`, and `TGA_MAX_SESSION_TURNS`. See `docs/LLM_CONFIG.md`.

## Run

Start the API:

```bash
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Start the Web application in another terminal:

```bash
cd apps/web
npm run dev
```

Create tasks through the Web UI or `POST /api/v2/tasks`. Every task follows the
same `Manager -> SessionCoordinator -> AgentSessionRunner -> ModelClient ->
ToolDispatcher -> Handler` path. There is no legacy execution fallback or provider-specific
fallback.

## Verify

```bash
python -m compileall -q tga apps tests
pytest -q
cd apps/web
npm test -- --reporter=dot
npm run build
npx playwright test --workers=1
```

Reports are read-model projections. `GET /api/v2/tasks/{task_id}/report`
renders without writing; `POST /api/v2/tasks/{task_id}/report/export` writes the
Markdown report under the task run directory.
