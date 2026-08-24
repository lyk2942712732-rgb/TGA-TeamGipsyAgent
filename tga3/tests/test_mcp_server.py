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
