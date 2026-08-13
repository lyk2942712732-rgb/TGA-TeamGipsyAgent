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

Every process reads and writes runtime configuration through the tracked
`runs2/.config` directory (or the equivalent `.config` directory selected by
`TGA2_RUN_ROOT`):

- `models.json` owns providers, models, verification state and API keys. API
  keys are intentionally stored as plain JSON for this competition project.
- `runtime.json` owns the four Solver roles, including each role's provider and
  model selection, prompts, tools, the unified Task/Intent/role budget, graph
  concurrency, Kali profile and file limits.
- `scenes.json` owns the five task scenes, their form fields, default policy and
  scene prompt additions.
- `mcp.json` owns MCP server definitions.

There is no second defaults directory and no implicit configuration migration.
All four JSON files must exist. This makes missing or stale deployment
configuration visible instead of silently creating another source of truth.

Configure a role's model on the Solver page. Creating a task no longer accepts
or stores a second per-task model assignment.

### `models.json`

`presets` is only the Models-page shortcut catalog (provider display name and
default OpenAI-compatible base URL). `providers` contains the providers the
user actually created. Each provider owns its models and one or more API keys:

```json
{
  "schema_version": 1,
  "presets": [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com"}
  ],
  "providers": [
    {
      "id": "provider_demo",
      "name": "My DeepSeek",
      "preset_id": "deepseek",
      "model_provider": "openai",
      "base_url": "https://api.deepseek.com",
      "models": [
        {"id": "model_chat", "name": "deepseek-chat", "verification_status": "verified"}
      ],
      "api_keys": [
        {"id": "key_main", "label": "Active", "api_key": "sk-plain-text"}
      ],
      "selected_api_key_id": "key_main"
    }
  ],
  "active_provider_id": "provider_demo",
  "active_model_id": "model_chat"
}
```

`model_provider: "openai"` means LangChain uses its OpenAI-compatible adapter;
it does not rename the provider to OpenAI. `selected_api_key_id` selects the
key used by that provider. The two top-level `active_*` fields are retained for
the Models-page compatibility endpoint; actual Solver execution chooses models
from `runtime.json -> roles -> <role> -> model`.

### Plan, Intent, Attempt and Round

A Plan is the Supervisor's versioned task-level strategy: an ordered set of
Intents plus its summary. An Intent is one executable and independently
reviewable work item inside that Plan. An Intent can have several Attempts; one
Attempt (Worker execution, Runtime evidence validation, Reviewer evaluation,
and Supervisor checkpoint decision) is one overall Round.

All limits are read from `runtime.json -> budget`. Models may propose actions,
but only Runtime code may consume budgets, revise Plan state, change Intent
state, or accept task completion.
