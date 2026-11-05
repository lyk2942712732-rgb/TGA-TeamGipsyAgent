"""One executor surface for blackboard and artifact operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .aoe_models import AOEQuestionInput
from .blackboard import Blackboard
from .config import TGA3Config
from .scout_intel import ScoutIntelMergeInput, ScoutIntelStore


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


def bound_identity(ctx: Context, *, scout: bool = False) -> tuple[UUID, str]:
    request = ctx.request_context.request
    if request is None:
        raise ValueError("A runtime-bound HTTP MCP connection is required")
    task_id, agent_id = request.headers.get("x-tga3-task-id"), request.headers.get("x-tga3-agent-id")
    if not task_id or not agent_id:
        raise ValueError("Runtime identity headers are missing")
    if scout and agent_id != "scout":
        raise ValueError("Scout tools require the bound Scout connection")
    return UUID(task_id), agent_id


def build_mcp(config: TGA3Config, blackboard: Blackboard) -> FastMCP:
    mcp = _new_mcp(config, "tga3-blackboard", config.runtime.mcp_instructions)
    # Workers author files; no agent-facing blackboard write tools.
    return mcp


def build_scout_mcp(config: TGA3Config, store: ScoutIntelStore, *, aoe=None) -> FastMCP:
    mcp = _new_mcp(config, "tga3-scout-intels", "Register question metadata and attachments incrementally, then finish collection."
                   if aoe is not None else "Extract Intel from /logs; save JSON using scout_write_intel. "
                   "Then consolidate all raw Intel using scout_merge_intel in the same session. "
                   "The system imports only merged files after full coverage. No blackboard publication tool.")

    if aoe is not None:
        @mcp.tool()
        async def scout_register_questions(questions: list[AOEQuestionInput], ctx: Context) -> dict[str, Any]:
            """AOE pre-phase only: register original question metadata and downloaded attachments.

            source_key is the original gateway ID or a stable label from user text. Preserve description,
            shared user constraints and submission requirements. Difficulty is supplied information only.
            attachments are existing local /inputs/... or /workspace/... files, not remote URLs.
            Multiple batches allowed; identical retries are deduplicated. Do not omit failed downloads.
            """
            task_id, _ = bound_identity(ctx, scout=True)
            return await aoe.register_questions(task_id, questions)

        @mcp.tool()
        async def scout_finish_collection(expected_count: int, ctx: Context) -> dict[str, Any]:
            """Close AOE collection only after the complete source list and all required files are registered.

            expected_count must equal the complete list count (including all gateway pages).
            Scout finishes after collection; there are no further AOE Scout rounds.
            """
            task_id, _ = bound_identity(ctx, scout=True)
            return await aoe.finish_collection(task_id, expected_count)

        return mcp

    @mcp.tool()
    async def scout_write_intel(action_id: str, claim: str, ctx: Context, detail: str = "",
                                topic: str = "general", evidence_paths: list[str] | None = None) -> dict[str, Any]:
        """Save a raw Intel from one recorded action in the supplied Scout materials.

        Supply action_id, claim and optional detail/topic. Task and source are runtime-bound.
        Omit evidence_paths to associate the recorded result body and all attachments automatically.
        If needed, select a nonempty subset of those recorded files. Other actions/files are rejected.
        Raw Intel is not registered as evidence; identical requests return the same stable ID.
        """
        task_id, _ = bound_identity(ctx, scout=True)
        return await store.write_action(task_id, action_id, claim, detail, topic, evidence_paths)

    @mcp.tool()
    async def scout_merge_intel(merged_from_ids: list[str], ctx: Context, claim: str | None = None,
                                detail: str | None = None, topic: str | None = None) -> dict[str, Any]:
        """Save a final Intel group chosen by the Scout.

        For a singleton submit only merged_from_ids:[original ID]; the server copies its exact body/topic.
        For multiple originals the Scout must write the consolidated claim/detail, preserving facts and scope.
        The server only binds task, validates IDs, unions sources/evidence and generates stable IDs.
        No automatic semantic grouping or generated prose. Each original must occur exactly once.
        """
        task_id, _ = bound_identity(ctx, scout=True)
        return await store.merge(ScoutIntelMergeInput(task_id=task_id, merged_from_ids=merged_from_ids,
                                                       claim=claim, detail=detail, topic=topic))

    return mcp


__all__ = ["build_mcp", "build_scout_mcp"]
