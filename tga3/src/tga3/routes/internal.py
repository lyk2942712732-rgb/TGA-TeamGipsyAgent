"""Internal worker-to-host checkpoint, WebSocket control gateway, and knowledge MCP relay routes."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Request, WebSocket

from .services import AppServices


def build_internal_router(services: AppServices) -> APIRouter:
    router = APIRouter()
    config = services.config
    coordinator = services.coordinator
    automation = services.automation
    delivery = services.delivery
    publications = services.publications
    submission_guard = services.submission_guard
    knowledge_relay = services.knowledge_relay

    @router.api_route("/knowledge-mcp/", methods=["GET", "POST", "DELETE"])
    async def knowledge_mcp(request: Request):
        return await knowledge_relay.forward(request)

    @router.post("/internal/blackboard/{task_id}/{agent_id}/checkpoint")
    async def blackboard_checkpoint(task_id: UUID, agent_id: str, body: dict) -> dict:
        worker = config.agents.agents[agent_id].role == "worker"
        scout = config.agents.agents[agent_id].role == "scout"
        if worker and (
            body.get("completion_hint")
            or body.get("submission_failure_action")
            or publications.final_pending(task_id, agent_id)
        ):
            await coordinator.aoe.protect_completion(task_id)
        protection = (
            await submission_guard.check(task_id, agent_id, str(body["submission_failure_action"]))
            if worker and body.get("submission_failure_action")
            else None
        )
        if worker and protection is None:
            protection = await submission_guard.recover(task_id, agent_id)
        file_sync = worker and bool(body.get("file_sync"))
        receipts = await publications.sync_findings(task_id, agent_id) if file_sync else []
        outbox_receipts = None
        if scout and body.get("outbox_sync"):
            imported = await coordinator.aoe.collection.import_outbox(task_id)
            if imported.get("imported") or imported.get("errors"):
                outbox_receipts = imported
        pending_final = file_sync and publications.final_pending(task_id, agent_id)
        result = await delivery.checkpoint(
            task_id,
            agent_id,
            ack=str(body.get("ack", "")),
            final=bool(body.get("final", False)) or pending_final,
            ack_only=bool(body.get("ack_only", False)),
            initial_only=bool(body.get("initial_only", False)),
        )
        if file_sync:
            receipts.extend(await publications.sync_final(task_id, agent_id))
        if receipts:
            result = {**result, "publication_receipts": receipts}
        if outbox_receipts:
            result = {**result, "question_import_receipts": outbox_receipts}
        if protection:
            result = {**result, "submission_protection": protection}
        if worker and not body.get("ack_only"):
            advice = await asyncio.to_thread(
                automation.experience.deliver,
                task_id,
                agent_id,
                str(body.get("experience_generation", "")),
                str(body.get("experience_ack", "")),
                body.get("experience_initial"),
            )
            if advice:
                result = {
                    **result,
                    "experience_context": advice["context"],
                    "experience_fingerprints": advice["fingerprints"],
                    "experience_delivery_id": advice["delivery_id"],
                }
        denied = (
            result.get("context")
            and (body.get("final") or body.get("force_start"))
            or body.get("defer_context")
            and any(result.get(k) for k in ["context", "experience_context"])
        )
        if worker and body.get("action_start") and not denied:
            await coordinator.aoe.action_started(task_id, agent_id, str(body["action_start"]))
        return result

    @router.websocket("/internal/agents/{task_id}/{agent_id}")
    async def agent_socket(websocket: WebSocket, task_id: UUID, agent_id: str) -> None:
        await coordinator.gateway.attach(task_id, agent_id, websocket)

    return router
