from __future__ import annotations

from pathlib import Path

from tga.capabilities.executor import CapabilityExecutor
from tga.capabilities.models import ActionSpec
from tga.tools.mcp_client import MCPCallResult


class FakeMCPClient:
    def call_tool(self, *, server, tool_name, arguments, volumes=None, timeout_seconds):
        return MCPCallResult(
            command=["fake", server.id, tool_name],
            stdout='{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"flag{tool}"}],"isError":false}}',
            stderr="",
            returncode=0,
        )


def make_hub(root: Path) -> Path:
    server_dir = root / "exploitation" / "searchsploit-mcp"
    server_dir.mkdir(parents=True)
    (server_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (server_dir / "server.py").write_text(
        """
Tool(
    name="searchsploit_search",
    description="search",
    inputSchema={
        "type": "object",
        "required": ["query"],
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
    },
)
""",
        encoding="utf-8",
    )
    return root


def spec(arguments):
    return ActionSpec(
        task_id="task_tool",
        solver_id="solver_a",
        action_id="tool_action",
        capability="tool.invoke",
        target="http://127.0.0.1:8080",
        scope=["127.0.0.1:8080"],
        flag_format=r"flag\{[^}]+\}",
        arguments=arguments,
    )


def test_tool_invoke_success_with_schema_validation(tmp_path):
    hub = make_hub(tmp_path / "hub")
    result = CapabilityExecutor(run_root=tmp_path / "runs", hub_root=hub, mcp_client=FakeMCPClient()).execute(
        spec(
            {
                "tool": "searchsploit",
                "mcp_tool": "searchsploit_search",
                "target": "http://127.0.0.1:8080",
                "arguments": {"query": "apache"},
            }
        )
    )

    assert result.status == "ok"
    assert result.artifacts
    assert result.candidate_flags == ["flag{tool}"]


def test_tool_invoke_rejects_unknown_method_and_bad_schema(tmp_path):
    hub = make_hub(tmp_path / "hub")
    executor = CapabilityExecutor(run_root=tmp_path / "runs", hub_root=hub, mcp_client=FakeMCPClient())

    unknown = executor.execute(
        spec(
            {
                "tool": "searchsploit",
                "mcp_tool": "not_real",
                "target": "http://127.0.0.1:8080",
                "arguments": {"query": "apache"},
            }
        )
    )
    bad_schema = executor.execute(
        spec(
            {
                "tool": "searchsploit",
                "mcp_tool": "searchsploit_search",
                "target": "http://127.0.0.1:8080",
                "arguments": {},
            }
        )
    )

    assert unknown.status == "blocked"
    assert unknown.error and unknown.error.code == "MCP_METHOD_NOT_AVAILABLE"
    assert bad_schema.status == "blocked"
    assert bad_schema.error and bad_schema.error.code == "MCP_SCHEMA_INVALID"
