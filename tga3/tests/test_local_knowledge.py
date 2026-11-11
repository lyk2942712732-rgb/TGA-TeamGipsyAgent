import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_aoe_runtime import setup
from test_aoe_sessions import environment
from test_docker_runtime import FakeContainers

from tga3.aoe_models import AOEQuestionInput
from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.docker_runtime import DockerContainerRuntime, FakeContainerRuntime, LaunchSpec
from tga3.host_agents import DeterministicHostModel
from tga3.local_knowledge import LocalKnowledge
from tga3.storage import InMemoryStorage
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.extra_cli import OpenCodeAdapter, QwenAdapter
from tga3.workers.prompts import worker_instructions


@pytest.fixture
def library(tmp_path, monkeypatch):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.ctf_wiki_root = str(tmp_path / "wiki")
    config.runtime.ctf_wiki_index = str(tmp_path / "wiki.sqlite3")
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    service = LocalKnowledge(config)
    docs = service.root / "docs"
    docs.mkdir(parents=True)
    (docs / "stack.md").write_text("# 栈溢出\n\n栈溢出保护 canary\n" + "evidence\n" * 250, encoding="utf8")
    (docs / "sql.md").write_text("# SQL injection\nSQL UNION injection reference", encoding="utf8")
    monkeypatch.setattr("tga3.local_knowledge.subprocess.run",
                        lambda *a, **kw: SimpleNamespace(stdout="fixture-revision\n"))
    return config, service


def test_offline_index_search_read_rebuild_and_missing_state(library):
    _, service = library
    assert not service.status()["available"]
    assert not service.index.exists()
    assert service.rebuild()["documents"] == 2
    assert service.search("栈溢出")["results"][0]["path"] == "stack.md"
    assert service.search("SQL injection")["results"][0]["path"] == "sql.md"
    assert service.search('" OR * DROP TABLE documents; --')["results"] == []
    read = service.read("stack.md", max_lines=10)
    assert read["next_line"] == 11 and len(read["content"].splitlines()) == 10
    assert service.read("stack.md", start_line=11)["start_line"] == 11
    with pytest.raises(ValueError, match="Unknown document"):
        service.read("../../config/models.json")
    (service.root / "docs/sql.md").unlink()
    assert service.rebuild()["documents"] == 1
    assert service.search("SQL")["results"] == []


@pytest.mark.parametrize("adapter_type", [CodexAdapter, ClaudeAdapter, OpenCodeAdapter, QwenAdapter])
def test_all_native_clients_select_only_local_knowledge(monkeypatch, tmp_path, adapter_type):
    environment(monkeypatch, tmp_path, claude=adapter_type is ClaudeAdapter)
    monkeypatch.setenv("TGA3_BASE_URL", "http://provider/v1")
    monkeypatch.setenv("TGA3_KNOWLEDGE_SOURCE", "local")
    monkeypatch.setenv("TGA3_CTF_KNOWLEDGE_MCP_URL", "http://control/local-knowledge-mcp/")
    adapter = adapter_type()
    args, env = adapter.command()
    if adapter_type is CodexAdapter:
        servers = tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"]
    elif adapter_type is ClaudeAdapter:
        servers = json.loads((tmp_path / ".tga3/mcp.json").read_text())["mcpServers"]
        assert "mcp__ctf_wiki__*" in args[args.index("--allowedTools") + 1]
    elif adapter_type is OpenCodeAdapter:
        servers = json.loads(Path(env["OPENCODE_CONFIG"]).read_text())["mcp"]
    else:
        servers = json.loads((Path(env["QWEN_HOME"]) / "settings.json").read_text())["mcpServers"]
    assert "ctf_wiki" in servers and "ctf42" not in servers
    assert "search_documents" in worker_instructions()
    assert "search_challenges" not in worker_instructions()


def test_workers_use_local_endpoint_but_other_roles_have_no_library(library):
    config, _ = library
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    for agent in config.agents.agents:
        runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=config.resolve_agent(agent), knowledge_source="local"))
        env = containers.kwargs["environment"]
        if config.agents.agents[agent].role == "worker":
            assert env["TGA3_KNOWLEDGE_SOURCE"] == "local"
            assert env["TGA3_CTF_KNOWLEDGE_MCP_URL"].endswith("/local-knowledge-mcp/")
        else:
            assert "TGA3_CTF_KNOWLEDGE_MCP_URL" not in env


async def test_aoe_inherits_local_choice_on_registration_and_launch(tmp_path):
    _, storage, runtime, coordinator, parent, _ = await setup(tmp_path, four_workers=True)
    await storage.update_task(parent.id, metadata={"knowledge_source": "local"})
    await coordinator.aoe.register_questions(parent.id, [
        AOEQuestionInput(source_key=str(i), title=str(i), description="original", difficulty="easy")
        for i in range(4)])
    assert all(q.metadata["knowledge_source"] == "local" for q in await storage.list_questions(parent.id))
    await coordinator.aoe.tick(parent.id)
    assert len(runtime.launched) == 4
    assert all(spec.knowledge_source == "local" for spec in runtime.launched)


def test_api_validates_choice_persists_it_and_serves_offline_mcp(library):
    config, service = library
    app = create_app(config, InMemoryStorage(), FakeContainerRuntime(), host_model=DeterministicHostModel())
    data = {"title": "fixture", "prompt": "fixture", "scene_id": "penetration_test"}
    with TestClient(app) as client:
        assert not client.get("/api/v3/knowledge-sources").json()["local"]["available"]
        assert client.post("/api/v3/tasks", data={**data, "knowledge_source": "bogus"}).status_code == 422
        assert client.post("/api/v3/tasks", data={**data, "knowledge_source": "local"}).status_code == 422
        service.rebuild()
        assert client.get("/api/v3/knowledge-sources").json()["local"]["documents"] == 2
        created = client.post("/api/v3/tasks", data={**data, "knowledge_source": "local"})
        assert created.status_code == 200
        assert created.json()["metadata"]["knowledge_source"] == "local"
        headers = {"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8083"}
        reply = client.post("/local-knowledge-mcp/", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search_documents", "arguments": {"query": "栈溢出"}}})
        assert reply.status_code == 200
        result = reply.json()["result"]
        assert not result.get("isError")
        assert "stack.md" in str(result)
