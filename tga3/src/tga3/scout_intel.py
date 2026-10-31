"""Durable Scout collection, followed by control-plane-owned blackboard handoff."""

from uuid import UUID

from .blackboard import Blackboard
from .domain import RunState, ScoutIntelPublication
from .errors import ConflictError, ContractError


class ScoutIntelCollector:
    def __init__(self, board: Blackboard):
        self.board = board
        self.storage = board.storage

    async def collect(self, request: ScoutIntelPublication) -> dict:
        task = await self.storage.get_task(request.task_id)
        if task.state != RunState.SCOUTING:
            raise ContractError("Scout Intel can be collected only during scouting")
        await self.storage.validate_artifact_refs(request.task_id, request.artifact_refs)
        payload = request.model_dump(mode="json")

        def save(metadata):
            if metadata.get("intel_collection_closed"):
                raise ContractError("Scout Intel collection is closed")
            drafts = dict(metadata.get("intel_drafts", {}))
            existing = drafts.get(request.idempotency_key)
            if existing is not None and existing != payload:
                raise ConflictError("Intel collection key already belongs to a different observation")
            order = list(metadata.get("intel_draft_order", drafts))
            if existing is None:
                order.append(request.idempotency_key)
            drafts[request.idempotency_key] = payload
            return {**metadata, "intel_drafts": drafts, "intel_draft_order": order, "intel_draft_count": len(drafts)}

        run = await self.storage.mutate_agent_metadata(request.task_id, "scout", save)
        return {"collected": True, "published": False, "collected_count": run.metadata["intel_draft_count"],
                "idempotency_key": request.idempotency_key}

    async def flush(self, task_id: UUID) -> int:
        run = await self.storage.mutate_agent_metadata(
            task_id, "scout", lambda metadata: {**metadata, "intel_collection_closed": True},
        )
        count = 0
        drafts = run.metadata.get("intel_drafts", {})
        for key in run.metadata.get("intel_draft_order", drafts):
            request = ScoutIntelPublication.model_validate(drafts[key])
            await self.board.publish(task_id, request.actor, request.publish_request())
            count += 1
        await self.storage.update_agent(task_id, "scout", metadata={"intel_flushed_count": count})
        return count
