from pathlib import Path

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.mcp_server import build_mcp
from tga3.skills import SkillCatalog
from tga3.storage import InMemoryStorage


def test_mcp_accepts_the_configured_container_gateway_host():
    config = TGA3Config(Path(__file__).parents[1] / "config")

    mcp = build_mcp(
        config,
        Blackboard(InMemoryStorage()),
        SkillCatalog(config.resolve_path(config.runtime.skills_root)),
    )

    security = mcp.settings.transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert "host.docker.internal:*" in security.allowed_hosts


async def test_mcp_publish_tool_exposes_one_discriminated_worker_contract():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    mcp = build_mcp(
        config,
        Blackboard(InMemoryStorage()),
        SkillCatalog(config.resolve_path(config.runtime.skills_root)),
    )

    tools = await mcp.list_tools()
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
