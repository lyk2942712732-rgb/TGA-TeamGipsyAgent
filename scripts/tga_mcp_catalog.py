from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_catalog import discover_mcp_security_hub


def main() -> int:
    parser = argparse.ArgumentParser(description="Print TGA's full mcp-security-hub catalog.")
    parser.add_argument(
        "--hub-root",
        default=os.environ.get("TGA_MCP_SECURITY_HUB_ROOT", "mcp-security-hub"),
        help="Path to a cloned FuzzingLabs/mcp-security-hub checkout.",
    )
    parser.add_argument("--summary", action="store_true", help="Print a compact text summary.")
    args = parser.parse_args()
    catalog = discover_mcp_security_hub(args.hub_root)
    if args.summary:
        print(f"hub_root={catalog.hub_root}")
        print(f"revision={catalog.revision or 'unknown'}")
        print(f"servers={len(catalog.servers)}")
        for server in catalog.servers:
            kind = "implemented" if server.implemented else "wrapper"
            print(f"{server.category}/{server.id} {kind} tools={len(server.tools)} image={server.image}")
    else:
        print(json.dumps(catalog.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

