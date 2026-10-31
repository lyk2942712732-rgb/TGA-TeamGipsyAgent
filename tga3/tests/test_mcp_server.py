import json
from pathlib import Path
from uuid import uuid4

import pytest

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
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
    assert [tool.name for tool in tools] == ["scout_write_intel"]
    fields = tools[0].inputSchema["$defs"]["ScoutIntelInput"]["properties"]
    assert set(fields) == {"task_id", "key", "claim", "detail", "topic", "evidence_paths"}


async def test_mcp_publish_tool_exposes_one_discriminated_worker_contract():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    mcp = build_mcp(
        config,
        Blackboard(InMemoryStorage()),
    )

    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "blackboard_sync", "blackboard_publish", "artifact_register",
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
    assert "artifact_refs" in finding_schema["required"]
    assert "artifact_refs" not in final_schema["properties"]


async def test_artifact_registration_uses_the_publishing_workers_private_directory(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.artifact_root = str(tmp_path)
    storage = InMemoryStorage()
    task = await storage.create_task("evidence", "pwn")
    mcp = build_mcp(config, Blackboard(storage))
    hashes = []
    for agent_id in ("worker-openai", "worker-claude"):
        root = tmp_path / str(task.id) / agent_id
        root.mkdir(parents=True)
        (root / "proof.txt").write_text(agent_id)
        arguments = {
            "task_id": str(task.id),
            "actor": {"agent_id": agent_id, "display_name": agent_id, "role": "worker"},
            "relative_path": "proof.txt", "name": "proof",
        }
        result = await mcp.call_tool("artifact_register", arguments)
        # FastMCP's return shape varies across supported releases.
        if isinstance(result, tuple):
            result = result[1]
        if not isinstance(result, dict):
            result = json.loads(result[0].text)
        assert Path(result["storage_path"]) == root / "proof.txt"
        hashes.append(result["sha256"])
        arguments["relative_path"] = "../outside.txt"
        with pytest.raises(Exception, match="escapes"):
            await mcp.call_tool("artifact_register", arguments)
    assert hashes[0] != hashes[1]
    with pytest.raises(Exception, match="only configured Scout or Workers"):
        await mcp.call_tool("artifact_register", {
            "task_id": str(uuid4()),
            "actor": {"agent_id": "reporter", "display_name": "Reporter", "role": "reporter"},
            "relative_path": "proof.txt", "name": "proof",
        })
