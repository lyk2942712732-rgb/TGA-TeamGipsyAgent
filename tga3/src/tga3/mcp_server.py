"""One executor surface for blackboard and artifact operations."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, AgentPublishRequest, Artifact, publication_contract
from .scout_intel import ScoutIntelInput, ScoutIntelStore


def _new_mcp(config: TGA3Config, name: str, instructions: str) -> FastMCP:
    hostname = urlsplit(config.runtime.blackboard_mcp_url).hostname
    if not hostname:
        raise ValueError("blackboard_mcp_url must be an absolute URL with a hostname")
    configured_host = f"[{hostname}]:*" if ":" in hostname else f"{hostname}:*"
    return FastMCP(
        name,
        instructions=instructions,
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


def build_mcp(config: TGA3Config, blackboard: Blackboard) -> FastMCP:
    mcp = _new_mcp(config, "tga3-blackboard", config.runtime.mcp_instructions)

    @mcp.tool()
    async def blackboard_sync(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read compact blackboard entries after a sequence number."""
        result = await blackboard.sync(UUID(task_id), after_seq=after_seq, limit=limit)
        return result.model_dump(mode="json")

    @mcp.tool(description=publication_contract("worker"))
    async def blackboard_publish(
        request: AgentPublishRequest,
    ) -> dict[str, Any]:
        """Publish one strictly typed Worker entry; the request schema is authoritative."""
        identity = request.actor
        configured = config.agents.agents.get(identity.agent_id)
        if configured is None or configured.role != identity.role:
            raise ValueError("actor must match a configured Scout or Worker")
        if request.kind.value == "final_candidate":
            run = await blackboard.storage.get_agent(request.task_id, identity.agent_id)
            task = await blackboard.storage.get_task(request.task_id)
            if run.metadata.get("blackboard_cursor", -1) < task.blackboard_seq:
                raise ValueError("Unread blackboard updates: run the native checkpoint before resubmitting")
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
        identity = Actor.model_validate(actor)
        configured = config.agents.agents.get(identity.agent_id)
        if configured is None or configured.role not in {"scout", "worker"} or identity.role != configured.role:
            raise ValueError("only configured Scout or Workers can register evidence")
        task_root = (config.resolve_path(config.runtime.artifact_root) / str(task_uuid) / identity.agent_id).resolve()
        path = (task_root / relative_path).resolve()
        if task_root != path and task_root not in path.parents:
            raise ValueError("artifact path escapes the task artifact directory")
        if not path.is_file():
            raise FileNotFoundError(f"artifact not found: {relative_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifact = Artifact(
            task_id=task_uuid,
            created_by=identity,
            name=name,
            storage_path=str(path),
            media_type=media_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
        )
        return (await blackboard.register_artifact(artifact)).model_dump(mode="json")

    return mcp


def build_scout_mcp(config: TGA3Config, store: ScoutIntelStore) -> FastMCP:
    mcp = _new_mcp(config, "tga3-scout-intels", "Extract Intel from /artifacts; save JSON using scout_write_intel. "
                   "The system imports all saved files after extraction. No blackboard publication tool is provided.")

    @mcp.tool()
    async def scout_write_intel(request: ScoutIntelInput) -> dict[str, Any]:
        """Save one validated Intel JSON in /intels during Scout's second phase.

        Fill request={task_id,key,claim,detail,topic,evidence_paths:[]}.
        key is stable for retries. evidence_paths are existing paths relative to /artifacts;
        the system handles artifact registration, JSON formatting and final blackboard import.
        """
        return await store.write(request)

    return mcp


__all__ = ["build_mcp", "build_scout_mcp"]
