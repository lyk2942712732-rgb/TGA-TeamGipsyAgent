from __future__ import annotations

import sys
from pathlib import Path

from tga.tools.mcp_catalog import MCPServerSpec
from tga.tools.mcp_client import MCPClient


class FakeCommandClient(MCPClient):
    def __init__(self, command: list[str]):
        super().__init__(prefer_compose=False)
        self.command = command

    def build_command(self, server, *, volumes=None):
        return self.command


def test_mcp_client_waits_for_tool_response(tmp_path: Path) -> None:
    server_py = tmp_path / "fake_mcp.py"
    server_py.write_text(
        """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}), flush=True)
    elif msg.get("method") == "tools/call":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "done"}]}}), flush=True)
""",
        encoding="utf-8",
    )
    client = FakeCommandClient([sys.executable, str(server_py)])
    server = MCPServerSpec(id="fake-mcp", category="test", path="fake", image="fake:latest")

    result = client.call_tool(server=server, tool_name="demo", arguments={}, timeout_seconds=5)

    assert result.ok
    assert '"id": 2' in result.stdout
    assert "done" in result.stdout
