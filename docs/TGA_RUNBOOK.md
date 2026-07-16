# TGA Runbook

Install:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
pytest -q
```

Run the executable v2 evaluation suite (it starts only local targets and emits
JSON metrics for success rate, Agent turns, tool calls, retries,
and duration):

```bash
python evals/run_eval.py
```

Run the Runtime UI checks:

```bash
cd apps/web
npm run build
npm test
npm run test:e2e
```

Run a demo:

```bash
tga run examples/web_ctf/task.json
```

Create, observe, control, and recover through the same v2 runtime:

```bash
tga create examples/web_ctf/task.json
tga start task_web_ctf_demo
tga status task_web_ctf_demo
tga observe task_web_ctf_demo --follow
tga cancel task_web_ctf_demo
tga resume task_web_ctf_demo
```

Equivalent script entrypoint:

```bash
python scripts/tga_run_demo.py --config examples/web_ctf/task.json
```

Check local tool availability:

```bash
python scripts/tga_mcp_healthcheck.py
```

Generate a report from an existing evidence database:

```bash
python scripts/tga_generate_report.py --db runs/task_web_ctf_demo/evidence.db --task-id task_web_ctf_demo --out runs/task_web_ctf_demo/reports/report.md
```

Demo configs:

- `examples/web_ctf/task.json` solves a local CTF-style web target and expects a `flag{...}` value.
- `examples/web_audit/task.json` audits a local web target and records confirmed findings only when evidence exists.
- `examples/code_audit/task.json` scans `examples/code_audit/sample_project` for code risks and secrets.

Week 1 limitations:

- The default subprocess worker is a safe placeholder unless B wires real tools for the intent.
- Reports are based on the evidence snapshot and do not independently verify findings.
