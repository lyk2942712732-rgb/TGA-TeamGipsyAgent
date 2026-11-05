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
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_ROOT", "/knowledge/des-ctf-knowledge")
    adapter = adapter_type()
    args, env = adapter.command()
    if adapter_type is CodexAdapter:
        config = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
        server = config["mcp_servers"]["des-ctf-knowledge"]
        assert server["startup_timeout_sec"] == 180
    else:
        config = json.loads((tmp_path / ".tga3/mcp.json").read_text())
        server = config["mcpServers"]["des-ctf-knowledge"]
        assert "mcp__des-ctf-knowledge__*" in args[args.index("--allowedTools") + 1]
        assert env["MCP_TIMEOUT"] == "180000"
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "tga3.workers.knowledge_server", "/knowledge/des-ctf-knowledge"]
    assert server["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    prompt = worker_instructions()
    assert "find_technique" in prompt and "SKILL.md once" in prompt and "no per-question" in prompt


def test_library_mount_is_readonly_for_workers_and_absent_for_scout_reporter(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    cfg.runtime.ctf_knowledge_root = str(tmp_path / "library")
    for provider in cfg.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    server = Path(cfg.runtime.ctf_knowledge_root) / "mcp/server.py"
    server.parent.mkdir(parents=True); server.write_text("# fixture")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = cfg, SimpleNamespace(containers=containers)
    for agent in ("worker-openai", "worker-claude", "scout", "reporter"):
        runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=cfg.resolve_agent(agent)))
        mounts = {v["bind"]: v["mode"] for v in containers.kwargs["volumes"].values()}
        if agent.startswith("worker"):
            assert mounts["/knowledge/des-ctf-knowledge"] == "ro"
            assert containers.kwargs["environment"]["TGA3_CTF_KNOWLEDGE_ROOT"] == "/knowledge/des-ctf-knowledge"
        else:
            assert "/knowledge/des-ctf-knowledge" not in mounts


@pytest.mark.asyncio
async def test_codex_knowledge_events_preserve_server_identity(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    adapter = CodexAdapter()
    events = []

    async def emit(method, params):
        events.append((method, params))

    await adapter.event({"type": "item.started", "item": {
        "id": "knowledge-call", "type": "mcp_tool_call",
        "server": "des-ctf-knowledge", "tool": "find_technique", "arguments": {"signal": "PNG"},
    }}, emit)
    assert events[0][0] == "agent.action.started"
    assert events[0][1]["server"] == "des-ctf-knowledge"
    assert events[0][1]["summary"] == "find_technique"
