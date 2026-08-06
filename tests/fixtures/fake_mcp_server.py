"""Official-SDK MCP stdio fixture used by integration tests."""

from __future__ import annotations

import sys

from mcp.server import MCPServer


server = MCPServer("tga-test-mcp")


@server.tool(description="Echo a required text value.", structured_output=False)
def echo(text: str, repeat: int = 1, token: str | None = None) -> str:
    del token
    print("fixture diagnostic", file=sys.stderr, flush=True)
    return text * repeat


@server.tool(description="Return a deterministic large text result.", structured_output=False)
def large_result(chars: int) -> str:
    return "x" * chars


if __name__ == "__main__":
    server.run()
