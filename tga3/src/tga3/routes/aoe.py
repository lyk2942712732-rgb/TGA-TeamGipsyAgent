"""AOE-specific question overview, extension, attachment download, and scoped event routes."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .services import AppServices, ExtendQuestionRequest


def build_aoe_router(services: AppServices) -> APIRouter:
    router = APIRouter()
    config, storage, coordinator = services.config, services.storage, services.coordinator

    async def owned_question(task_id: UUID, question_id: UUID):
        question = await storage.get_task(question_id)
        if question.parent_task_id != task_id:
            raise ValueError("题目不属于当前 AOE 任务")
        return question

    @router.get("/tasks/{task_id}/questions")
    async def question_overview(task_id: UUID) -> dict:
        return await coordinator.aoe.snapshot(task_id)

    @router.post("/tasks/{task_id}/questions/{question_id}/extend")
    async def extend_question(task_id: UUID, question_id: UUID, body: ExtendQuestionRequest) -> dict:
        return await coordinator.aoe.extend_question_attempt(task_id, question_id, minutes=body.minutes)

    @router.get("/tasks/{task_id}/questions/{question_id}/files/{file_id}")
    async def question_file(task_id: UUID, question_id: UUID, file_id: UUID):
        await owned_question(task_id, question_id)
        item = next((file for file in await storage.list_input_files(question_id) if file.id == file_id), None)
        if item is None:
            raise KeyError("题目附件不存在")
        path = Path(item.storage_path)
        if path.is_symlink() or not path.resolve().is_relative_to(config.task_path(question_id, "inputs").resolve()):
            raise ValueError("题目附件路径不可用")
        return FileResponse(path, filename=item.name, media_type=item.media_type)

    @router.get("/tasks/{task_id}/aoe/events")
    async def question_events(
        task_id: UUID,
        question_id: UUID | None = None,
        agent_id: str | None = None,
        limit: int = 500,
    ) -> dict:
        parent = await storage.get_task(task_id)
        if parent.scene_id != "aoe" or parent.parent_task_id is not None:
            raise ValueError("当前任务不是 AOE 批次")
        limit = min(max(limit, 1), 10000)
        questions = (
            [await owned_question(task_id, question_id)] if question_id else await storage.list_questions(task_id)
        )
        result = []
        has_more = False
        for target in [parent, *questions]:
            has_more |= target.event_seq > limit
            messages = await storage.list_events(target.id, after_seq=max(0, target.event_seq - limit), limit=limit)
            for message in messages:
                identity = message.question_id or message.payload.get("question_id")
                if question_id and str(identity) != str(question_id):
                    continue
                if agent_id and not (
                    message.channel_agent_id == agent_id
                    or message.actor.agent_id == agent_id
                    or message.payload.get("agent_id") == agent_id
                ):
                    continue
                result.append(message)
        result.sort(key=lambda item: (item.created_at, str(item.id)))
        has_more |= len(result) > limit
        return {"events": [item.model_dump(mode="json") for item in result[-limit:]], "has_more": has_more}

    return router
