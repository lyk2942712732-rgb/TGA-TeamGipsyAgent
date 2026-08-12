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
