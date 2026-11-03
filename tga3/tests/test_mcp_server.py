import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.domain import RunState
from tga3.mcp_server import build_mcp, build_scout_mcp
from tga3.scout_intel import ScoutIntelStore
from tga3.storage import InMemoryStorage


def test_mcp_accepts_the_configured_container_gateway_host():
    config = TGA3Config(Path(__file__).parents[1] / "config")

    mcp = build_mcp(
        config,
        Blackboard(InMemoryStorage()),
    )

    security = mcp.settings.transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert "host.docker.internal:*" in security.allowed_hosts


async def test_scout_mcp_exposes_only_json_writer_and_no_publication_tool():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    board = Blackboard(InMemoryStorage())
    tools = await build_scout_mcp(config, ScoutIntelStore(config, board)).list_tools()
    assert [tool.name for tool in tools] == ["scout_write_intel", "scout_merge_intel"]
    merge_fields = tools[1].inputSchema["$defs"]["ScoutIntelMergeInput"]["properties"]
    assert set(merge_fields) == {"task_id", "key", "claim", "detail", "topic", "merged_from_keys"}
    fields = tools[0].inputSchema["$defs"]["ScoutIntelInput"]["properties"]
    assert set(fields) == {"task_id", "key", "claim", "detail", "topic", "evidence_paths", "source_action_ids"}


async def test_mcp_publish_tool_exposes_one_discriminated_worker_contract():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    mcp = build_mcp(
        config,
        Blackboard(InMemoryStorage()),
    )

    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "blackboard_sync", "blackboard_publish", "finding_publish",
    }
    publish = next(tool for tool in tools if tool.name == "blackboard_publish")
    request = publish.inputSchema["properties"]["request"]

    assert request["discriminator"]["propertyName"] == "kind"
    assert set(request["discriminator"]["mapping"]) == {"finding", "final_candidate"}
    assert len(request["oneOf"]) == 2
    assert "body={claim, detail?}" in publish.description
    finding_schema = publish.inputSchema["$defs"]["WorkerFindingPublication"]
    final_schema = publish.inputSchema["$defs"]["WorkerFinalCandidatePublication"]
    assert "kind" in finding_schema["required"]
    assert "evidence_paths" in finding_schema["required"]
    assert "artifact_refs" not in finding_schema["properties"]
    assert "artifact_refs" not in final_schema["properties"]
    simple = next(tool for tool in tools if tool.name == "finding_publish")
    assert set(simple.inputSchema["required"]) == {"action_id", "claim", "scope"}
    assert set(simple.inputSchema["properties"]) == {"action_id", "claim", "scope", "preconditions"}


def result_payload(result):
    if isinstance(result, tuple):
        result = result[1]
    if not isinstance(result, dict):
        result = json.loads(result[0].text)
    return result


async def test_finding_publication_registers_private_evidence_and_deduplicates_retries(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("evidence", "pwn")
    board = Blackboard(storage)
    mcp = build_mcp(config, board)
    hashes = []
    for agent_id in ("worker-openai", "worker-claude"):
        root = config.task_path(task.id, "workspaces", agent_id)
        root.mkdir(parents=True)
        (root / "proof.txt").write_text(agent_id)
        request = {
            "kind": "finding", "task_id": str(task.id),
            "actor": {"agent_id": agent_id, "display_name": agent_id, "role": "worker"},
            "body": {"claim": "verified fixture"}, "evidence_paths": ["proof.txt", "./proof.txt"],
            "idempotency_key": "fixture",
        }
        result = result_payload(await mcp.call_tool("blackboard_publish", {"request": request}))
        links = await board.inspect_finding(task.id, UUID(result["id"]))
        assert len(links) == 1
        evidence = storage.artifacts[links[0].artifact_id]
        assert Path(evidence.storage_path) == root / "proof.txt"
        hashes.append(evidence.sha256)
        retried = result_payload(await mcp.call_tool("blackboard_publish", {"request": request}))
        assert retried["id"] == result["id"]
        assert len(storage.artifacts) == len(hashes)
    assert hashes[0] != hashes[1]


@pytest.mark.parametrize("relative", ["../outside.txt", "/etc/passwd", "C:/secret", "missing.txt", "nested/../../x"])
async def test_finding_rejects_invalid_paths_before_registering_any_evidence(tmp_path, relative):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("evidence", "pwn")
    root = config.task_path(task.id, "workspaces", "worker-openai")
    root.mkdir(parents=True)
    (root / "proof.txt").write_text("proof")
    mcp = build_mcp(config, Blackboard(storage))
    with pytest.raises(Exception, match="Evidence"):
        await mcp.call_tool("blackboard_publish", {"request": {
            "kind": "finding", "task_id": str(task.id),
            "actor": {"agent_id": "worker-openai", "display_name": "Worker", "role": "worker"},
            "body": {"claim": "fixture"}, "evidence_paths": ["proof.txt", relative],
            "idempotency_key": "bad",
        }})
    assert not storage.artifacts
    assert not await storage.list_entries(task.id)


async def test_closed_task_rejects_finding_before_creating_evidence_records(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("closed", "pwn")
    await storage.update_task(task.id, state=RunState.COMPLETED)
    mcp = build_mcp(config, Blackboard(storage))
    with pytest.raises(Exception, match="closed"):
        await mcp.call_tool("blackboard_publish", {"request": {
            "kind": "finding", "task_id": str(task.id),
            "actor": {"agent_id": "worker-openai", "display_name": "Worker", "role": "worker"},
            "body": {"claim": "fixture"}, "evidence_paths": ["proof.txt"], "idempotency_key": "closed",
        }})
    assert not storage.artifacts


@pytest.mark.skipif(os.name == "nt", reason="Linux evidence symlink isolation")
async def test_finding_rejects_symlink_to_another_workers_evidence(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("symlink", "pwn")
    root = config.task_path(task.id, "workspaces", "worker-openai")
    other = config.task_path(task.id, "workspaces", "worker-claude")
    root.mkdir(parents=True)
    other.mkdir(parents=True)
    (other / "proof.txt").write_text("other worker")
    (root / "proof.txt").symlink_to(other / "proof.txt")
    mcp = build_mcp(config, Blackboard(storage))
    request = {"kind": "finding", "task_id": str(task.id),
               "actor": {"agent_id": "worker-openai", "display_name": "Worker", "role": "worker"},
               "body": {"claim": "fixture"}, "evidence_paths": ["proof.txt"], "idempotency_key": "escape"}
    with pytest.raises(Exception, match="Evidence"):
        await mcp.call_tool("blackboard_publish", {"request": request})
    (root / "proof.txt").unlink()
    root.rmdir()
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        await mcp.call_tool("blackboard_publish", {"request": request})
    assert not storage.artifacts


async def test_finding_infers_action_sources_from_its_workspace_evidence(tmp_path):
    from tga3.action_logs import read_action_log
    from tga3.workers.action_journal import ActionJournal

    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("action sources", "pwn")
    root = config.task_path(task.id, "workspaces", "worker-openai")
    logs = config.task_path(task.id, "logs", "worker-openai")
    journal = ActionJournal(logs, identity={"task_id": str(task.id), "agent_id": "worker-openai"},
                            output_directory=root)
    journal.record("item_0", "shell", result={"aggregated_output": "verified"}, status="completed")
    action = read_action_log(logs)["actions"][0]
    assert action["action_id"].startswith("worker-openai-action-")
    mcp = build_mcp(config, Blackboard(storage))
    request = {"kind": "finding", "task_id": str(task.id),
               "actor": {"agent_id": "worker-openai", "display_name": "Worker", "role": "worker"},
               "body": {"claim": "verified"}, "evidence_paths": [action["artifact"]["output_path"]],
               "idempotency_key": "source-fixture"}
    result = result_payload(await mcp.call_tool("blackboard_publish", {"request": request}))
    assert result["source_action_ids"] == [action["action_id"]]
    assert (await storage.list_entries(task.id))[0].source_action_ids == [action["action_id"]]
    request.update(source_action_ids=["worker-claude-action-unknown"], idempotency_key="invalid-source")
    with pytest.raises(Exception, match="unknown action"):
        await mcp.call_tool("blackboard_publish", {"request": request})
    assert len(storage.artifacts) == 1
