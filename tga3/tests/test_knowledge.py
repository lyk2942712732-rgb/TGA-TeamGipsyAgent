"""The hosted distribution must never activate a knowledge connector."""
import json
import tomllib

import pytest
from test_aoe_sessions import environment

from tga3.knowledge import DEFAULT_MCP_URL, worker_knowledge_server
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.prompts import worker_instructions


@pytest.mark.parametrize("role", ["worker", "scout", "reporter"])
@pytest.mark.parametrize("url", ["", "https://example.invalid/mcp"])
def test_knowledge_cannot_be_reenabled_with_environment(monkeypatch, role, url):
    monkeypatch.setenv("TGA3_AGENT_ROLE", role)
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_MCP_URL", url)
    assert DEFAULT_MCP_URL == ""
    assert worker_knowledge_server() is None


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter])
def test_native_cli_configs_exclude_knowledge_and_public_search(monkeypatch, tmp_path, adapter_type):
    environment(monkeypatch, tmp_path, claude=adapter_type is ClaudeAdapter)
    monkeypatch.setenv("TGA3_HOSTED", "1")
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_MCP_URL", "https://example.invalid/mcp")
    adapter = adapter_type()
    args, _ = adapter.command()
    if adapter_type is CodexAdapter:
        config = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
        servers = config.get("mcp_servers", {})
        assert config["web_search"] == "disabled"
    else:
        config = json.loads((tmp_path / ".tga3/mcp.json").read_text())
        servers = config.get("mcpServers", {})
        assert "WebSearch" not in args[args.index("--tools") + 1]
    assert not any("knowledge" in name or "ctf42" in name for name in servers)
    prompt = worker_instructions()
    assert "No public knowledge base" in prompt
    assert "search_challenges" not in prompt
