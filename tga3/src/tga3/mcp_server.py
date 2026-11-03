"""One executor surface for blackboard and artifact operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .action_findings import ActionFindingInput, ActionFindingPublisher
from .action_logs import resolve_action_sources
from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, AgentPublishRequest, EntryKind, publication_contract
from .evidence import register_evidence
from .scout_intel import ScoutIntelInput, ScoutIntelMergeInput, ScoutIntelStore


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
    findings = ActionFindingPublisher(config, blackboard)

    @mcp.tool()
    async def finding_publish(action_id: str, claim: str, scope: str, ctx: Context,
                              preconditions: str = "") -> dict[str, Any]:
        """Immediately share one reusable fact from a tool result, without rereading logs.

        action_id: exact TGA action ID supplied after execution (never a native tool-call ID).
        claim: verified observation useful to another Worker, even before a final answer exists.
        scope: tested target, inputs and conditions; limit negative results to what was tested.
        preconditions: optional prerequisites not already shown in the command (e.g. a prior login).
        Task/actor, method, exit status, result body, attachments and retry key are supplied by the server.
        Check saved=true and entry.id. On failure correct the reported issue and retry the same fact;
        identical arguments return the same Finding. Never submit a publication action as evidence.
        """
        http_request = ctx.request_context.request
        if http_request is None:
            raise ValueError("finding_publish requires the Worker-bound HTTP MCP connection")
        task_id = http_request.headers.get("x-tga3-task-id")
        agent_id = http_request.headers.get("x-tga3-agent-id")
        if not task_id or not agent_id:
            raise ValueError("Worker identity headers are missing; reconnect using the runtime MCP configuration")
        return await findings.publish(UUID(task_id), agent_id, ActionFindingInput(
            action_id=action_id, claim=claim, scope=scope, preconditions=preconditions,
        ))

    @mcp.tool()
    async def blackboard_sync(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
        """Read compact blackboard entries after a sequence number."""
        result = await blackboard.sync(UUID(task_id), after_seq=after_seq, limit=limit)
        return result.model_dump(mode="json")

    @mcp.tool(description="Submit final_candidate with existing Finding IDs. For observations use the simpler "
              "finding_publish(action_id, claim, scope) instead. Legacy request schema: "
              + publication_contract("worker"))
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
        actor = Actor.model_validate(request.actor)
        await blackboard.check_write(request.task_id, actor, request.kind)
        references = None
        if request.kind == EntryKind.FINDING:
            root = config.task_path(request.task_id, "workspaces", actor.agent_id)
            request.source_action_ids = resolve_action_sources(
                config.task_path(request.task_id, "logs", actor.agent_id), request.task_id, actor.agent_id,
                request.source_action_ids, request.evidence_paths,
            )
            references = await register_evidence(blackboard, request.task_id, actor, root, request.evidence_paths)
        entry = await blackboard.publish(request.task_id, actor, request.publish_request(artifact_refs=references))
        return entry.model_dump(mode="json")

    return mcp


def build_scout_mcp(config: TGA3Config, store: ScoutIntelStore) -> FastMCP:
    mcp = _new_mcp(config, "tga3-scout-intels", "Extract Intel from /logs; save JSON using scout_write_intel. "
                   "Then consolidate all raw Intel using scout_merge_intel in the same session. "
                   "The system imports only merged files after full coverage. No blackboard publication tool.")

    @mcp.tool()
    async def scout_write_intel(request: ScoutIntelInput) -> dict[str, Any]:
        """Save one validated raw Intel JSON in /intels/raw during Scout's second work phase.

        Fill request={task_id,key,claim,detail,topic,source_action_ids:[],evidence_paths:[]}.
        key is stable for retries. source_action_ids identify records in /logs/scout-actions.json;
        evidence_paths are relative to /workspace;
        the system handles artifact registration and JSON formatting. Raw files remain private;
        the third work phase merges them before final blackboard import.
        """
        return await store.write(request)

    @mcp.tool()
    async def scout_merge_intel(request: ScoutIntelMergeInput) -> dict[str, Any]:
        """Save one consolidated Intel JSON in /intels/merged during Scout's third work phase.

        Fill request={task_id,key,claim,detail,topic,merged_from_keys:[raw Intel keys]}.
        Merge repeated/overlapping facts about the same object under the same conditions.
        Preserve all distinct facts, tested conditions, failure boundaries and later corrections.
        An unrelated raw Intel stays separate by referencing its single key.
        Each raw key must appear exactly once across final Intel. Do not provide action IDs,
        evidence paths or artifact UUIDs: the server unions them from the originals.
        Original /intels/raw JSON is retained. Check saved=true. No direct blackboard publication.
        """
        return await store.merge(request)

    return mcp


__all__ = ["build_mcp", "build_scout_mcp"]
