# TGA2 backend

TGA2 contains product state and the LangChain/LangGraph runtime. It deliberately
does not contain HTTP routes or frontend code.

The boundaries are:

- `apps/web`: React UI.
- `apps/api`: the only FastAPI application and HTTP contract.
- `tga2/core`: tasks, evidence, policy, persistence, workspace and reports.
- `tga2/agent`: LangGraph topology and LangChain agents/middleware/tools.
- `tga2/integrations`: standard model and MCP adapters.

Run the complete application from the repository root:

```powershell
python -m pip install -e .
$env:TGA2_RUN_ROOT = "runs2"
uvicorn apps.api.main:app --reload
```

The Vite development server proxies `/api` to port 8000:

```powershell
cd apps/web
npm install
npm run dev
```

With no API key, tasks use the deterministic offline agent suite but still pass
through the same LangGraph, evidence store and report flow.

## Configuration source of truth

Every process reads and writes runtime configuration through
`<TGA2_RUN_ROOT>/.config`:

- `models.json` owns providers, models, verification state and API keys. API
  keys are intentionally stored as plain JSON for this competition project.
- `runtime.json` owns the four Solver roles, including each role's provider and
  model selection, prompts, tools, graph limits, Kali profile and file limits.
- `scenes.json` owns the five task scenes, their form fields, default policy and
  scene prompt additions.
- `mcp.json` owns MCP server definitions.

Files in `tga2/defaults` are installation seeds only. They are copied into a
new run root on first start and are not an alternative live configuration
source. Legacy `model.json` and `model-registry.json` are read only once when
`models.json` does not yet exist.

Configure a role's model on the Solver page. Creating a task no longer accepts
or stores a second per-task model assignment.
