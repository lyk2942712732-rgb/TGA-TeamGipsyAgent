from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tga.capabilities.registry import CapabilityRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description="Print TGA capability registry snapshot.")
    parser.add_argument("--project-root", default=os.getcwd())
    parser.add_argument("--hub-root", default=os.environ.get("TGA_MCP_SECURITY_HUB_ROOT"))
    args = parser.parse_args()
    registry = CapabilityRegistry(project_root=args.project_root, hub_root=args.hub_root)
    print(json.dumps(registry.snapshot(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
