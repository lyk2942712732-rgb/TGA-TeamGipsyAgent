import os
from pathlib import Path
from uuid import uuid4

import pytest

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.domain import Actor
from tga3.evidence import register_evidence
from tga3.mcp_server import build_mcp, build_scout_mcp
from tga3.scout_intel import ScoutIntelStore
from tga3.storage import InMemoryStorage


def configured():
    return TGA3Config(Path(__file__).parents[1] / "config")


def test_mcp_accepts_the_configured_container_gateway_host():
    mcp = build_mcp(configured(), Blackboard(InMemoryStorage()))
    security = mcp.settings.transport_security
    assert security.enable_dns_rebinding_protection is True
    assert "host.docker.internal:*" in security.allowed_hosts


async def test_scout_mcp_uses_generated_ids_and_no_model_assigned_keys():
    config = configured()
    tools = await build_scout_mcp(config, ScoutIntelStore(config, Blackboard(InMemoryStorage()))).list_tools()
    assert [tool.name for tool in tools] == ["scout_write_intel", "scout_merge_intel"]
    fields = tools[0].inputSchema["properties"]
    assert set(fields) == {"action_id", "claim", "detail", "topic", "evidence_paths"}
    fields = tools[1].inputSchema["properties"]
    assert set(fields) == {"claim", "detail", "topic", "merged_from_ids"}


async def test_worker_tools_remove_unused_sync_and_legacy_finding_branch():
    mcp = build_mcp(configured(), Blackboard(InMemoryStorage()))
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert tools == {}
    for name in ["finding_publish", "blackboard_publish", "blackboard_sync"]:
        with pytest.raises(Exception, match="Unknown tool"):
            await mcp.call_tool(name, {})



@pytest.mark.parametrize("relative", ["../outside.txt", "/etc/passwd", "C:/secret", "missing.txt", "nested/../../x"])
async def test_evidence_rejects_invalid_paths_before_registering_any_file(tmp_path, relative):
    storage = InMemoryStorage()
    task = await storage.create_task("evidence", "pwn")
    (tmp_path / "proof.txt").write_text("proof")
    with pytest.raises(Exception, match="Evidence"):
        await register_evidence(Blackboard(storage), task.id,
            Actor(agent_id="worker-openai", display_name="Worker", role="worker"),
            tmp_path, ["proof.txt", relative])
    assert not storage.artifacts


@pytest.mark.skipif(os.name == "nt", reason="Linux evidence symlink isolation")
async def test_evidence_rejects_symlinks_outside_the_agent_workspace(tmp_path):
    root, other = tmp_path / "own", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    (other / "proof.txt").write_text("other worker")
    (root / "proof.txt").symlink_to(other / "proof.txt")
    storage = InMemoryStorage()
    task = await storage.create_task("symlink", "pwn")
    actor = Actor(agent_id="worker-openai", display_name="Worker", role="worker")
    with pytest.raises(Exception, match="Evidence"):
        await register_evidence(Blackboard(storage), task.id, actor, root, ["proof.txt"])
    assert not storage.artifacts
