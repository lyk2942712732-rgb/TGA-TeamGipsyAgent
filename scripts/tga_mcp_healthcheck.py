from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_config import configured_mcp_path
from tga.tools.mcp_manager import MCPManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Start, initialize and list tools for configured MCP servers.")
    parser.add_argument("--config", default=str(configured_mcp_path()), help="Path to mcp.json.")
    parser.add_argument("--cache", help="Optional discovery cache path.")
    args = parser.parse_args()
    manager = MCPManager(config_path=args.config, cache_path=args.cache)
    manager.refresh()
    status = manager.status_snapshot()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    failed = status["config_error"] or any(item["enabled"] and not item["discovered"] for item in status["records"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
