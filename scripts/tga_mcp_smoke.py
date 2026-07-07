from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_catalog import discover_mcp_security_hub
from tga.tools.mcp_client import MCPClient


DEFAULT_CASES = [
    ("searchsploit-mcp", "searchsploit_search", {"query": "apache"}),
    ("solazy-mcp", "list_runs", {}),
    ("yara-mcp", "list_active_scans", {}),
    ("nmap-mcp", "list_active_scans", {}),
    ("boofuzz-mcp", "boofuzz_list_scripts", {}),
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe stdio smoke calls against selected mcp-security-hub servers.")
    parser.add_argument("--hub-root", required=True, help="Local checkout path for mcp-security-hub.")
    parser.add_argument("--report-path", help="Optional path to write the JSON smoke report.")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help='Optional case as server:tool:json_args, for example searchsploit-mcp:searchsploit_search:{"query":"apache"}.',
    )
    args = parser.parse_args()

    catalog = discover_mcp_security_hub(args.hub_root)
    client = MCPClient(hub_root=catalog.hub_root)
    cases = parse_cases(args.case) if args.case else DEFAULT_CASES

    results = []
    for server_id, tool_name, tool_args in cases:
        server = catalog.get(server_id)
        if server is None:
            results.append({"server": server_id, "tool": tool_name, "status": "missing_server"})
            continue
        result = client.call_tool(
            server=server,
            tool_name=tool_name,
            arguments=tool_args,
            timeout_seconds=args.timeout_seconds,
        )
        results.append(
            {
                "server": server.id,
                "tool": tool_name,
                "status": "ok" if result.ok and not mcp_response_is_error(result.stdout) else "failed",
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "command": result.command,
                "stdout_tail": result.stdout[-3000:],
                "stderr_tail": result.stderr[-3000:],
            }
        )

    report = {"hub_root": str(Path(args.hub_root).resolve()), "cases": results}
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(output, encoding="utf-8")
    print(output)
    return 1 if any(item.get("status") != "ok" for item in results) else 0


def parse_cases(items: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    cases = []
    for item in items:
        server_id, tool_name, raw_args = item.split(":", 2)
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            raise ValueError(f"case args must be a JSON object: {item}")
        cases.append((server_id, tool_name, args))
    return cases


def mcp_response_is_error(stdout: str) -> bool:
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != 2:
            continue
        result = message.get("result")
        if isinstance(result, dict):
            return bool(result.get("isError"))
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
