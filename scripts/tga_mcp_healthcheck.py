from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.tools.mcp_healthcheck import check_mcp_security_hub, records_to_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Healthcheck all mcp-security-hub servers known to TGA.")
    parser.add_argument(
        "--hub-root",
        default=os.environ.get("TGA_MCP_SECURITY_HUB_ROOT", "mcp-security-hub"),
        help="Path to a cloned FuzzingLabs/mcp-security-hub checkout.",
    )
    args = parser.parse_args()
    records = check_mcp_security_hub(Path(args.hub_root))
    print(records_to_json(records))
    return 1 if any(record.status == "failed" for record in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
