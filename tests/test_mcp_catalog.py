from __future__ import annotations

from pathlib import Path

from tga.tools.mcp_catalog import discover_mcp_security_hub, parse_readme_tools


def test_discovers_all_dockerfile_mcp_servers(tmp_path: Path) -> None:
    root = tmp_path / "hub"
    server = root / "web-security" / "demo-mcp"
    wrapper = root / "reconnaissance" / "wrap-mcp"
    server.mkdir(parents=True)
    wrapper.mkdir(parents=True)
    (server / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (wrapper / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (server / "server.py").write_text(
        """
from mcp.types import Tool

def list_tools():
    return [Tool(name="scan_target", description="scan", inputSchema={"type": "object"})]
""",
        encoding="utf-8",
    )
    (root / "docker-compose.yml").write_text(
        """
services:
  demo-mcp:
    image: demo-mcp:latest
  wrap-mcp:
    image: wrap-mcp:latest
    profiles: ["on-demand"]
volumes:
  output:
""",
        encoding="utf-8",
    )

    catalog = discover_mcp_security_hub(root)

    assert [item.id for item in catalog.servers] == ["wrap-mcp", "demo-mcp"]
    demo = catalog.get("demo")
    assert demo is not None
    assert demo.implemented is True
    assert demo.tools[0].name == "scan_target"
    assert catalog.resolve_server_for_tool("scan_target") == demo
    assert catalog.get("wrap").profiles == ["on-demand"]


def test_parse_wrapper_readme_tools(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        """
# Wrapper

## Tools

| Tool | Description |
|------|-------------|
| `security_check` | Quick scan |
| semgrep_scan | Full scan |
""",
        encoding="utf-8",
    )

    tools = parse_readme_tools(readme)

    assert [tool.name for tool in tools] == ["security_check", "semgrep_scan"]
