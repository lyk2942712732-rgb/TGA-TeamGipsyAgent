# TGA Runbook

Install:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
pytest -q
```

Run a demo:

```bash
python scripts/tga_run_demo.py --config examples/web_ctf/task.json
```

Check local tool availability:

```bash
python scripts/tga_mcp_healthcheck.py
```

