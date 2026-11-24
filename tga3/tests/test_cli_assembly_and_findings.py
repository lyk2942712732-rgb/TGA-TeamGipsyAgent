"""Tests for decoupled CLI runtime assembly, relaxed Finding schema, and Worker cadence nudges."""

from __future__ import annotations

import json
import shutil
import time
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tga3.action_findings import DEFAULT_FINDING_SCOPE, ActionFindingInput, ActionFindingPublisher
from tga3.app import create_app
from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import AgentRun, AgentState, RunState
from tga3.errors import ContractError
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal
from tga3.workers.adapter_factory import create_adapter
from tga3.workers.blackboard_hook import worker_finding_nudge
from tga3.workers.cli_adapter import ClaudeAdapter, CodexAdapter
from tga3.workers.extra_cli import OpenCodeAdapter, QwenAdapter
from tga3.workers.extra_cli import servers as extra_cli_servers

CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "config"


def _make_config(tmp_path: Path) -> tuple[TGA3Config, Path]:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_SOURCE, config_dir)
    models = json.loads((config_dir / "models.json").read_text(encoding="utf-8"))
    for provider in models["providers"]:
        provider["api_keys"][0]["api_key"] = "test-key"
    (config_dir / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")
    runtime = json.loads((config_dir / "runtime.json").read_text(encoding="utf-8"))
    runtime["task_root"] = str(tmp_path / "tasks")
    (config_dir / "runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    return TGA3Config(config_dir), config_dir


def test_solver_cli_runtime_assembly_persists_and_routes_images(tmp_path):
    config, config_dir = _make_config(tmp_path)
    app = create_app(config=config, storage=InMemoryStorage(), containers=FakeContainerRuntime())
    with TestClient(app) as client:
        runtimes = client.get("/api/v3/runtimes")
        assert runtimes.status_code == 200
        rt_map = {item["id"]: item for item in runtimes.json()}
        assert set(rt_map) == {"codex_cli", "claude_cli", "opencode_cli", "qwen_cli"}
        assert rt_map["claude_cli"]["image"] == "tga3-worker-claude:local"
        assert rt_map["qwen_cli"]["supported_protocols"] == [
            "openai_chat_completions",
            "openai_responses",
            "anthropic",
        ]

        agents = client.get("/api/v3/config/agents").json()
        updated = deepcopy(agents)
        # Re-assemble Scout onto Claude Code CLI and Reporter onto Qwen Code CLI.
        updated["agents"]["scout"].update(
            runtime="claude_cli",
            provider_id="anthropic",
            model_id="claude-worker-model",
            protocol="anthropic",
        )
        updated["agents"]["reporter"].update(
            runtime="qwen_cli",
            provider_id="openai",
            model_id="openai-worker-model",
            protocol="openai_chat_completions",
        )
        saved = client.put("/api/v3/config/agents", json=updated)
        assert saved.status_code == 200
        assert saved.json()["agents"]["scout"]["runtime"] == "claude_cli"
        assert saved.json()["agents"]["reporter"]["runtime"] == "qwen_cli"

        # Verify image routing follows assembled runtime, not hardcoded agent_id.
        assert config.image_for_runtime("claude_cli", "scout") == "tga3-worker-claude:local"
        assert config.image_for_runtime("qwen_cli", "reporter") == "tga3-worker-qwen:local"

        defs = {item["id"]: item for item in client.get("/api/v3/agent-definitions").json()}
        assert defs["scout"]["runtime"] == "claude_cli"
        assert defs["scout"]["image"] == "tga3-worker-claude:local"
        assert "Bash" in defs["scout"]["tools"]
        assert defs["reporter"]["runtime"] == "qwen_cli"
        assert defs["reporter"]["image"] == "tga3-worker-qwen:local"

        # Invalid runtime/protocol combo must still fail validation cleanly.
        invalid = deepcopy(updated)
        invalid["agents"]["scout"]["protocol"] = "openai_responses"
        assert client.put("/api/v3/config/agents", json=invalid).status_code == 422

        # Reload from disk to confirm clean persistence.
        reloaded = TGA3Config(config_dir)
        assert reloaded.agents.agents["scout"].runtime == "claude_cli"
        assert reloaded.agents.agents["reporter"].runtime == "qwen_cli"


def test_per_task_runtime_override_resolves_assembled_cli(tmp_path):
    config, _ = _make_config(tmp_path)
    resolved = config.resolve_agent(
        "worker-openai",
        "anthropic",
        "claude-worker-model",
        "anthropic",
        runtime="claude_cli",
    )
    assert resolved.runtime == "claude_cli"
    assert resolved.protocol == "anthropic"
    assert config.image_for_runtime(resolved.runtime, resolved.agent_id) == "tga3-worker-claude:local"


def test_adapter_factory_and_multi_cli_scout_reporter_support(tmp_path, monkeypatch):
    base_env = {
        "TGA3_TASK_ID": str(uuid4()),
        "TGA3_AGENT_ID": "scout",
        "TGA3_AGENT_DISPLAY_NAME": "Scout",
        "TGA3_AGENT_ROLE": "scout",
        "TGA3_SCOUT_PHASE": "collecting",
        "TGA3_SCOUT_INTEL_MCP_URL": "http://host.docker.internal:8083/aoe-scout-mcp/",
        "TGA3_WORKSPACE": str(tmp_path / "ws"),
        "TGA3_LOGS": str(tmp_path / "logs"),
        "TGA3_MODEL_NAME": "test-model",
        "TGA3_API_KEY": "sk-test",
        "TGA3_BASE_URL": "https://api.example/v1",
        "TGA3_SYSTEM_PROMPT": "System test prompt",
    }
    for k, v in base_env.items():
        monkeypatch.setenv(k, v)

    monkeypatch.setenv("TGA3_AGENT_RUNTIME", "codex_cli")
    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "openai_responses")
    assert isinstance(create_adapter("codex_cli"), CodexAdapter)

    monkeypatch.setenv("TGA3_AGENT_RUNTIME", "claude_cli")
    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "anthropic")
    claude = create_adapter("claude_cli")
    assert isinstance(claude, ClaudeAdapter)
    claude_args, _ = claude.command()
    mcp_cfg = json.loads((claude.state_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "intel" in mcp_cfg["mcpServers"]
    assert "mcp__intel__*" in claude_args[claude_args.index("--allowedTools") + 1]

    monkeypatch.setenv("TGA3_PROVIDER_PROTOCOL", "openai_chat_completions")
    monkeypatch.setenv("TGA3_AGENT_RUNTIME", "opencode_cli")
    opencode = create_adapter("opencode_cli")
    monkeypatch.setenv("TGA3_AGENT_RUNTIME", "qwen_cli")
    qwen = create_adapter("qwen_cli")
    assert isinstance(opencode, OpenCodeAdapter)
    assert isinstance(qwen, QwenAdapter)
    assert "intel" in extra_cli_servers(opencode)
    assert "intel" in extra_cli_servers(qwen)

    # Verify reporter instructions work on non-Codex CLIs.
    monkeypatch.setenv("TGA3_AGENT_ROLE", "reporter")
    monkeypatch.setenv("TGA3_AGENT_ID", "reporter")
    reporter_qwen = create_adapter("qwen_cli")
    assert "Return only Markdown." in reporter_qwen.role_instructions()
    assert reporter_qwen.scout_intel_mcp_url() is None


@pytest.mark.asyncio
async def test_relaxed_finding_auto_binds_latest_action_and_multi_action_synthesis(tmp_path):
    config, _ = _make_config(tmp_path)
    storage = InMemoryStorage()
    board = Blackboard(storage)
    publisher = ActionFindingPublisher(config, board)

    task = await storage.create_task("Finding test", "penetration_test")
    await storage.update_task(task.id, state=RunState.RUNNING)
    await storage.upsert_agent(
        AgentRun(
            task_id=task.id,
            agent_id="worker-openai",
            sdk="codex_cli",
            desired_state=AgentState.RUNNING,
            actual_state=AgentState.RUNNING,
            provider_id="openai",
            model_id="openai-worker-model",
            protocol="openai_responses",
        )
    )
    workspace = config.task_path(task.id, "workspaces", "worker-openai")
    logs = config.task_path(task.id, "logs", "worker-openai")
    workspace.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    journal = ActionJournal(
        logs,
        identity={"task_id": str(task.id), "agent_id": "worker-openai", "runtime": "codex_cli"},
        output_directory=workspace,
    )
    a1 = journal.record(
        "c1",
        "exec_command",
        inputs={"cmd": "nmap -sV target"},
        result={"out": "8080 open"},
        status="completed",
    )
    a2 = journal.record(
        "c2",
        "exec_command",
        inputs={"cmd": "curl target:8080/debug"},
        result={"out": "200 debug"},
        status="completed",
    )

    # 1. Minimal payload using alias "summary", omitting action_id and scope, plus extra ignored keys.
    minimal = ActionFindingInput.model_validate(
        {"summary": "Port 8080 exposes /debug endpoint", "confidence": "high"}
    )
    assert minimal.claim == "Port 8080 exposes /debug endpoint"
    assert minimal.scope == DEFAULT_FINDING_SCOPE
    assert minimal.action_id == ""
    res1 = await publisher.publish(task.id, "worker-openai", minimal)
    assert res1["saved"] is True
    assert res1["action_id"] == a2["action_id"]
    assert res1["entry"]["source_action_ids"] == [a2["action_id"]]
    assert DEFAULT_FINDING_SCOPE in res1["entry"]["body"]["detail"]

    # 2. Multi-step synthesis referencing multiple action_ids.
    multi = ActionFindingInput.model_validate(
        {
            "claim": "Port scan and HTTP probe confirm /debug info leak",
            "action_ids": [a1["action_id"], a2["action_id"]],
            "scope": "Anonymous GET against port 8080",
        }
    )
    res2 = await publisher.publish(task.id, "worker-openai", multi)
    assert res2["action_ids"] == [a1["action_id"], a2["action_id"]]
    assert res2["entry"]["source_action_ids"] == [a1["action_id"], a2["action_id"]]
    assert a1["artifact"]["output_path"] in res2["entry"]["body"]["detail"]
    assert a2["artifact"]["output_path"] in res2["entry"]["body"]["detail"]
    links = await storage.finding_links(task.id, UUID(res2["entry"]["id"]))
    assert {link.locator for link in links} == {
        a1["artifact"]["output_path"],
        a2["artifact"]["output_path"],
    }

    # 3. Explicit unknown action_id must still raise ContractError strictly.
    bad = ActionFindingInput.model_validate({"action_id": "non-existent-action", "claim": "Bad ref"})
    with pytest.raises(ContractError):
        await publisher.publish(task.id, "worker-openai", bad)


def test_worker_finding_cadence_nudge_and_aoe_handoff_reminder(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    findings = tmp_path / "findings"
    workspace.mkdir()
    findings.mkdir()
    monkeypatch.setenv("TGA3_AGENT_ROLE", "worker")
    monkeypatch.setenv("TGA3_WORKSPACE", str(workspace))
    monkeypatch.setenv("TGA3_FINDINGS", str(findings))
    monkeypatch.setenv("TGA3_TASK_ID", "task-1")
    monkeypatch.delenv("TGA3_AOE_PARENT_ID", raising=False)
    monkeypatch.delenv("TGA3_ATTEMPT_DEADLINE", raising=False)
    monkeypatch.setenv("TGA3_FINDING_NUDGE_STEPS", "6")

    record = {"action_id": "a1", "action": {"tool": "exec_command"}}
    for _ in range(5):
        assert worker_finding_nudge(record) == ""
    nudge_6 = worker_finding_nudge(record)
    assert "[阶段沉淀提醒]" in nudge_6
    assert "6 步" in nudge_6

    # Writing a finding resets the counter.
    (findings / "f1.json").write_text('{"claim":"found"}', encoding="utf-8")
    assert worker_finding_nudge(record) == ""
    for _ in range(5):
        assert worker_finding_nudge(record) == ""
    assert "[阶段沉淀提醒]" in worker_finding_nudge(record)

    # AOE handoff reminder fires once when deadline <= 150s and no final candidate exists.
    monkeypatch.setenv("TGA3_AOE_PARENT_ID", "batch-1")
    monkeypatch.setenv("TGA3_ATTEMPT_ID", "attempt-2")
    monkeypatch.setenv("TGA3_ATTEMPT_DEADLINE", str(time.time() + 90))
    handoff = worker_finding_nudge(record)
    assert "[交棒沉淀提醒]" in handoff
    # Must not repeat on subsequent tool call in the same attempt.
    assert worker_finding_nudge(record) == ""
