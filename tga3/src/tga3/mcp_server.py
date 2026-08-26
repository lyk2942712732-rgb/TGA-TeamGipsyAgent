"""One executor surface for blackboard, artifact and skill operations."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, Artifact, WorkerPublishRequest, worker_publish_contract
from .skills import SkillCatalog


def build_mcp(config: TGA3Config, blackboard: Blackboard, skills: SkillCatalog) -> FastMCP:
    hostname = urlsplit(config.runtime.blackboard_mcp_url).hostname
    if not hostname:
        raise ValueError("blackboard_mcp_url must be an absolute URL with a hostname")
    configured_host = f"[{hostname}]:*" if ":" in hostname else f"{hostname}:*"
    mcp = FastMCP(
        "tga3-blackboard",
        instructions=config.runtime.mcp_instructions,
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                configured_host,
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )

    @mcp.tool()
    async def blackboard_sync(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read compact blackboard entries after a sequence number."""
        result = await blackboard.sync(UUID(task_id), after_seq=after_seq, limit=limit)
        return result.model_dump(mode="json")

    @mcp.tool(description=worker_publish_contract())
    async def blackboard_publish(
        request: WorkerPublishRequest,
    ) -> dict[str, Any]:
        """Publish one strictly typed Worker entry; the request schema is authoritative."""
        entry = await blackboard.publish(
            request.task_id,
            Actor.model_validate(request.actor),
            request.publish_request(),
        )
        return entry.model_dump(mode="json")

    @mcp.tool()
    async def artifact_register(
        task_id: str,
        actor: dict[str, Any],
        relative_path: str,
        name: str,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Register a file already written below /artifacts; returns an artifact id for a Finding."""
        task_uuid = UUID(task_id)
        task_root = (config.resolve_path(config.runtime.artifact_root) / str(task_uuid)).resolve()
        path = (task_root / relative_path).resolve()
        if task_root != path and task_root not in path.parents:
            raise ValueError("artifact path escapes the task artifact directory")
        if not path.is_file():
            raise FileNotFoundError(f"artifact not found: {relative_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = Artifact(
            task_id=task_uuid,
            created_by=Actor.model_validate(actor),
            name=name,
            storage_path=str(path),
            media_type=media_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
        )
        return (await blackboard.register_artifact(artifact)).model_dump(mode="json")

    @mcp.tool()
    def skills_list() -> list[dict[str, Any]]:
        """List skill packages by name, summary and Markdown document count."""
        return [
            {"name": item.name, "description": item.description, "file_count": item.file_count}
            for item in skills.list()
        ]

    @mcp.tool()
    def skill_read(name: str, path: str = "SKILL.md") -> str:
        """Read one document from a skill package. Start with SKILL.md, then follow its routing guidance."""
        return skills.read(name, path)

    return mcp


__all__ = ["build_mcp"]
