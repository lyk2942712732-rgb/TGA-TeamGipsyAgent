# TGA - Team Gipsy Agent

TGA is a Week 1 MVP for an authorized vulnerability-review and CTF-solving agent.

The project is intentionally small:

- shared contracts in `tga/contracts.py`
- scope and evidence gates in `tga/core/`
- worker and tool wrappers in `tga/workers/` and `tga/tools/`
- SQLite evidence and filesystem artifacts in `tga/evidence/`
- Markdown reporting in `tga/reporting/`

## Quick Start

```bash
python -m pip install -e ".[dev]"
pytest -q
python scripts/tga_run_demo.py --config examples/web_ctf/task.json
```

Generated runs are written under `runs/`.

## Week 1 Rule

No confirmed result without evidence:

- CTF flags must match the configured format and appear in real output or artifact text.
- Vulnerability findings must reference an artifact and pass the finding evidence gate.
- Active tools must pass scope and intensity policy.

