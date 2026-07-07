from __future__ import annotations

import json

from tga.tools.mcp_healthcheck import local_tool_healthcheck


def main() -> int:
    print(json.dumps(local_tool_healthcheck(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

