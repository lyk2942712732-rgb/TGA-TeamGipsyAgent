"""One executor surface for blackboard, artifact and skill operations."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, Artifact, ArtifactRef, PublishRequest
from .skills import SkillCatalog


def build_mcp(config: TGA3Config, blackboard: Blackboard, skills: SkillCatalog) -> FastMCP:
    mcp = FastMCP(
        "tga3-blackboard",
        instructions=config.runtime.mcp_instructions,
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    async def blackboard_sync(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read compact blackboard entries after a sequence number."""
        result = await blackboard.sync(UUID(task_id), after_seq=after_seq, limit=limit)
        return result.model_dump(mode="json")

    @mcp.tool()
    async def blackboard_publish(
        task_id: str,
        actor: dict[str, Any],
        kind: str,
        body: dict[str, Any],
        idempotency_key: str,
        topic: str = "general",
        artifact_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Publish one contract-checked entry; Finding references are checked atomically."""
        entry = await blackboard.publish(
            UUID(task_id),
            Actor.model_validate(actor),
            PublishRequest(
                kind=kind,
                body=body,
                topic=topic,
                artifact_refs=[ArtifactRef.model_validate(item) for item in artifact_refs or []],
                idempotency_key=idempotency_key,
            ),
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
    def skills_list() -> list[dict[str, str]]:
        """List universal skills by name and short description."""
        return [{"name": item.name, "description": item.description} for item in skills.list()]

    @mcp.tool()
    def skill_read(name: str) -> str:
        """Read the complete SKILL.md for a selected skill name."""
        return skills.read(name)

    return mcp


__all__ = ["build_mcp"]
