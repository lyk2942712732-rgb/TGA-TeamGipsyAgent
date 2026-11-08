import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_aoe_sessions import environment
from test_docker_runtime import FakeContainers

from tga3.config import TGA3Config
from tga3.docker_runtime import DockerContainerRuntime, LaunchSpec
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.prompts import worker_instructions


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter])
def test_knowledge_is_connected_to_both_native_cli_configs(monkeypatch, tmp_path, adapter_type):
    environment(monkeypatch, tmp_path, claude=adapter_type is ClaudeAdapter)
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_MCP_URL", "https://ctf42.online/mcp")
    adapter = adapter_type()
    args, env = adapter.command()
    if adapter_type is CodexAdapter:
        config = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
        server = config["mcp_servers"]["ctf42"]
        assert server["startup_timeout_sec"] == 15
        assert server["tool_timeout_sec"] == 30
        assert server["required"] is False
        assert "des-ctf-knowledge" not in config["mcp_servers"]
    else:
        config = json.loads((tmp_path / ".tga3/mcp.json").read_text())
        server = config["mcpServers"]["ctf42"]
        assert server["type"] == "http"
        assert "mcp__ctf42__*" in args[args.index("--allowedTools") + 1]
        assert env["MCP_TIMEOUT"] == "15000"
        assert "des-ctf-knowledge" not in config["mcpServers"]
    assert server["url"] == "https://ctf42.online/mcp"
    assert not {"command", "args", "headers", "http_headers", "env"} & server.keys()
    prompt = worker_instructions()
    assert "search_challenges" in prompt and "EXTERNAL LINKS" in prompt and "no per-question" in prompt
    assert "find_technique" not in prompt and "Des-CTF" not in prompt


def test_online_knowledge_only_enabled_for_workers_and_legacy_library_not_mounted(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    cfg.runtime.ctf_knowledge_root = str(tmp_path / "library")
    for provider in cfg.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    server = Path(cfg.runtime.ctf_knowledge_root) / "mcp/server.py"
    server.parent.mkdir(parents=True)
    server.write_text("# fixture")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = cfg, SimpleNamespace(containers=containers)
    for agent in ("worker-openai", "worker-claude", "scout", "reporter"):
        runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=cfg.resolve_agent(agent)))
        mounts = {v["bind"]: v["mode"] for v in containers.kwargs["volumes"].values()}
        assert "/knowledge/des-ctf-knowledge" not in mounts
        assert "TGA3_CTF_KNOWLEDGE_ROOT" not in containers.kwargs["environment"]
        if agent.startswith("worker"):
            expected = cfg.runtime.blackboard_mcp_url.rstrip('/').removesuffix('/mcp') + '/knowledge-mcp/'
            assert containers.kwargs["environment"]["TGA3_CTF_KNOWLEDGE_MCP_URL"] == expected
        else:
            assert "TGA3_CTF_KNOWLEDGE_MCP_URL" not in containers.kwargs["environment"]


@pytest.mark.asyncio
async def test_codex_knowledge_events_preserve_server_identity(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    adapter = CodexAdapter()
    events = []

    async def emit(method, params):
        events.append((method, params))

    await adapter.event({"type": "item.started", "item": {
        "id": "knowledge-call", "type": "mcp_tool_call",
        "server": "ctf42", "tool": "search_challenges", "arguments": {"query": "PNG"},
    }}, emit)
    assert events[0][0] == "agent.action.started"
    assert events[0][1]["server"] == "ctf42"
    assert events[0][1]["summary"] == "search_challenges"


@pytest.mark.parametrize("role", ["worker", "scout", "reporter"])
def test_disabled_remote_does_not_restore_legacy_stdio_server(monkeypatch, role):
    from tga3.knowledge import worker_knowledge_server

    monkeypatch.setenv("TGA3_AGENT_ROLE", role)
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_ROOT", "/knowledge/des-ctf-knowledge")
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_MCP_URL", "")
    assert worker_knowledge_server() is None


def test_legacy_setting_remains_readable_but_online_url_is_the_active_default():
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.ctf_knowledge_root = "old-library"
    assert cfg.runtime.ctf_knowledge_mcp_url == "https://ctf42.online/mcp"
    assert "ctf_knowledge_root" not in cfg.runtime.model_dump()
