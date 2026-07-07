"""MCP client placeholder.

Week 1 keeps this thin so the team can connect FuzzingLabs/mcp-security-hub
without forcing the rest of the system to depend on a specific transport.
"""

from __future__ import annotations


class MCPClient:
    def list_tools(self) -> list[str]:
        return []

    def call_tool(self, name: str, arguments: dict) -> dict:
        raise NotImplementedError("MCP transport is not configured yet")

