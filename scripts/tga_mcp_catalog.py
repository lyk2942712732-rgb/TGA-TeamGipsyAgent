from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_config import configured_mcp_path
from tga.tools.mcp_manager import MCPManager


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Discover MCP tools from explicit mcp.json configuration.")
    parser.add_argument("--config", default=str(configured_mcp_path()), help="Path to mcp.json.")
    parser.add_argument("--cache", help="Optional discovery cache path.")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    manager = MCPManager(config_path=args.config, cache_path=args.cache)
    snapshot = manager.refresh()
    status = manager.status_snapshot()
    payload = {
        **status,
        "tools": [route.model_dump(mode="json") for route in snapshot.routes],
    }
    if args.summary:
        print(f"config={status['config_path']}")
        print(f"catalog_version={snapshot.version}")
        for record in status["records"]:
            print(
                f"{record['server']} configured={record['configured']} reachable={record['reachable']} "
                f"discovered={record['discovered']} tools={record['tools']}"
            )
        for route in snapshot.routes:
            print(f"{route.provider_name} -> {route.server_id}.{route.method}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if status["config_error"] or any(item["enabled"] and not item["discovered"] for item in status["records"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
