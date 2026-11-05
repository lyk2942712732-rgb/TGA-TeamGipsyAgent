"""Run the pinned upstream MCP server with accelerated safe YAML parsing.

Upstream indexes every WP before its MCP handshake. PyYAML's Python parser can
exceed the native CLI's initialization request deadline on a small VM. Its C
safe loader preserves the safe constructor rules and avoids that startup cost.
The knowledge checkout and upstream tools remain unchanged.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import yaml


def main() -> None:
    if hasattr(yaml, "CSafeLoader"):
        yaml.SafeLoader = yaml.CSafeLoader
    runpy.run_path(str(Path(sys.argv[1]) / "mcp/server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
