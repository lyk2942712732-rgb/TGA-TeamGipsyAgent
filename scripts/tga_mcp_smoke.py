from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.contracts import TGATask
from tga.tools.mcp_config import configured_mcp_path
from tga.tools.mcp_manager import MCPManager
from tga.tools.mcp_policy import redact_sensitive


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one configured MCP method through the production manager.")
    parser.add_argument("--config", default=str(configured_mcp_path()), help="Path to mcp.json.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", default="{}", help="JSON object passed to the MCP method.")
    parser.add_argument("--target", default="http://127.0.0.1")
    parser.add_argument("--workspace")
    parser.add_argument("--report-path")
    args = parser.parse_args()
    arguments = json.loads(args.arguments)
    if not isinstance(arguments, dict):
        parser.error("--arguments must decode to a JSON object")
    workspace = Path(args.workspace).resolve() if args.workspace else None
    manager = MCPManager(config_path=args.config)
    snapshot = manager.refresh(workspace=workspace)
    route = next((item for item in snapshot.routes if item.server_id == args.server and item.method == args.tool), None)
    if route is None:
        print(json.dumps({"ok": False, "error": "configured method was not discovered"}, ensure_ascii=False))
        return 1
    task = TGATask(
        id="mcp_smoke",
        name="MCP smoke",
        mode="ctf",
        target=args.target,
        goal="verify configured MCP tool",
        allow_active_scan=True,
        intensity="active",
    )
    outcome = manager.call_tool(
        task=task,
        route=route,
        arguments=arguments,
        catalog_version=snapshot.version,
        workspace=workspace,
    )
    report = redact_sensitive(outcome.model_dump(mode="json"))
    for key in ("raw_result_json", "stdout", "stderr"):
        report[key] = _redact_text(str(report.get(key) or ""))
    # Host command/args are intentionally absent from the report and model result.
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(output, encoding="utf-8")
    print(output)
    return 0 if outcome.ok else 1


def _redact_text(value: str) -> str:
    return re.sub(
        r'(?i)(["\']?(?:authorization|cookie|token|secret|password|api[_-]?key)["\']?\s*[:=]\s*)["\']?[^"\'\s,}]+',
        r'\1"[REDACTED]"',
        value,
    )


if __name__ == "__main__":
    raise SystemExit(main())
