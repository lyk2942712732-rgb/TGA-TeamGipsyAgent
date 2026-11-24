"""Core task lifecycle, blackboard, events, experience, writeup, and worker control routes."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..domain import USER_ACTOR, AgentState, PublishRequest, RunState
from ..knowledge import KnowledgeSource
from .services import AppServices, ModelRequest, RealtimePromptRequest, RestartTaskRequest


def build_tasks_router(services: AppServices) -> APIRouter:
    router = APIRouter()
    config = services.config
    storage = services.storage
    coordinator = services.coordinator
    automation = services.automation
    usage = services.usage

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "tga3"}

    @router.get("/tasks")
    async def list_tasks(limit: int = 200) -> list[dict[str, Any]]:
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        return [task.model_dump(mode="json") for task in tasks]

    @router.get("/attention")
    async def attention(limit: int = 200) -> list[dict[str, Any]]:
        """Runtime attention queue for pauses and failures."""
        result: list[dict[str, Any]] = []
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        for task in tasks:
            if task.state == RunState.FAILED:
                result.append(
                    {
                        "id": f"task:{task.id}:failed",
                        "kind": "task_failed",
                        "task_id": str(task.id),
                        "task_title": task.title,
                        "task_state": task.state,
                        "agent_id": None,
                        "title": "任务运行失败",
                        "detail": "打开任务查看错误和 Agent 状态。",
                        "created_at": task.updated_at,
                    }
                )
            for agent in await storage.list_agents(task.id):
                if agent.actual_state not in {AgentState.PAUSED, AgentState.PAUSE_REQUESTED, AgentState.FAILED}:
                    continue
                result.append(
                    {
                        "id": f"agent:{task.id}:{agent.agent_id}:{agent.actual_state}",
                        "kind": "agent_failed" if agent.actual_state == AgentState.FAILED else "agent_paused",
                        "task_id": str(task.id),
                        "task_title": task.title,
                        "task_state": task.state,
                        "agent_id": agent.agent_id,
                        "title": "Agent 运行失败" if agent.actual_state == AgentState.FAILED else "Agent 已暂停",
                        "detail": agent.last_error or "可恢复该 Agent，或打开任务补充提示。",
                        "created_at": agent.updated_at,
                    }
                )
        return sorted(result, key=lambda item: str(item["created_at"]), reverse=True)

    @router.post("/tasks")
    async def create_task(
        title: Annotated[str, Form(min_length=1, max_length=500)],
        prompt: Annotated[str, Form(min_length=1)],
        scene_id: Annotated[str, Form(min_length=1, max_length=100)],
        agent_models: Annotated[str, Form()] = "{}",
        aoe_attempt_minutes: Annotated[int, Form(ge=1, le=1440)] = 20,
        aoe_first_attempt_minutes: Annotated[int | None, Form(ge=1, le=1440)] = None,
        aoe_second_attempt_minutes: Annotated[int | None, Form(ge=1, le=1440)] = None,
        aoe_joint_minutes: Annotated[int | None, Form(ge=1, le=1440)] = None,
        aoe_auto_submit_flag: Annotated[bool, Form()] = True,
        knowledge_source: Annotated[KnowledgeSource, Form()] = "online",
        produce_report: Annotated[bool, Form()] = True,
        files: Annotated[list[UploadFile] | None, File()] = None,
    ) -> dict[str, Any]:
        raw_overrides = json.loads(agent_models)
        if not isinstance(raw_overrides, dict):
            raise ValueError("agent_models must be an object")
        overrides = {agent_id: ModelRequest.model_validate(value) for agent_id, value in raw_overrides.items()}
        initial_files = [
            (
                file.filename or "upload.bin",
                file.content_type or "application/octet-stream",
                await file.read(),
            )
            for file in files or []
        ]
        task = await coordinator.create_and_start(
            title,
            prompt,
            scene_id,
            initial_files,
            model_overrides={
                agent_id: (value.provider_id, value.model_id, value.protocol, value.runtime)
                for agent_id, value in overrides.items()
            },
            aoe_attempt_minutes=aoe_attempt_minutes,
            aoe_first_attempt_minutes=aoe_first_attempt_minutes,
            aoe_second_attempt_minutes=aoe_second_attempt_minutes,
            aoe_joint_minutes=aoe_joint_minutes,
            aoe_auto_submit_flag=aoe_auto_submit_flag,
            knowledge_source=knowledge_source,
            produce_report=produce_report,
        )
        return task.model_dump(mode="json")

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: UUID) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        await usage.refresh_task(task_id)
        await coordinator.orchestrator_for(task).refresh_token_usage(task, usage)
        agents = await storage.list_agents(task_id)

        def historical_model_name(agent) -> str:
            try:
                return config.models.provider(agent.provider_id).model(agent.model_id, agent.protocol).name
            except (KeyError, ValueError):
                return agent.model_id or "未配置"

        return {
            "task": task.model_dump(mode="json"),
            "experience_count": len(automation.experience.cards(task_id)),
            "agents": [
                {
                    **item.model_dump(mode="json"),
                    "metadata": {
                        k: v
                        for k, v in item.metadata.items()
                        if k not in {"token_usage_sources", "intel_drafts", "intel_draft_order"}
                    },
                    "display_name": config.agents.agents[item.agent_id].display_name,
                    "role": config.agents.agents[item.agent_id].role,
                    "model_name": historical_model_name(item),
                    "runtime_location": (
                        "container"
                        if config.agents.agents[item.agent_id].role in {"scout", "worker", "reporter"}
                        else "reserved"
                    ),
                }
                for item in agents
            ],
        }

    @router.post("/tasks/{task_id}/files")
    async def upload_file(task_id: UUID, file: UploadFile) -> dict[str, Any]:
        item, entry_id = await coordinator.add_input_file(
            task_id,
            file.filename or "upload.bin",
            file.content_type or "application/octet-stream",
            await file.read(),
        )
        return {"file": item.model_dump(mode="json"), "blackboard_entry_id": str(entry_id)}

    @router.get("/tasks/{task_id}/blackboard")
    async def blackboard(task_id: UUID, after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        return (await coordinator.blackboard.sync(task_id, after_seq=after_seq, limit=limit)).model_dump(mode="json")

    @router.get("/tasks/{task_id}/experience")
    async def experience_cards(task_id: UUID) -> dict:
        await storage.get_task(task_id)
        cards, states = await asyncio.gather(
            asyncio.to_thread(automation.experience.cards, task_id),
            asyncio.to_thread(automation.experience.states, task_id),
        )
        return {"cards": cards, "state": states}

    @router.get("/tasks/{task_id}/experience/{card_id}/resources/{relative:path}")
    async def experience_resource(task_id: UUID, card_id: str, relative: str) -> FileResponse:
        await storage.get_task(task_id)
        path = await asyncio.to_thread(automation.experience.resource, task_id, card_id, relative)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @router.post("/tasks/{task_id}/blackboard/prompts")
    async def realtime_prompt(task_id: UUID, body: RealtimePromptRequest) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        if task.state not in {RunState.SCOUTING, RunState.RUNNING}:
            raise ValueError("当前任务已停止接收实时提示")
        entry = await coordinator.blackboard.publish(
            task_id,
            USER_ACTOR,
            PublishRequest(
                kind="user_prompt",
                topic="realtime",
                body={"text": body.text},
                idempotency_key=f"realtime:{body.idempotency_key}",
            ),
        )
        await coordinator.orchestrator_for(task).on_realtime_prompt(task, body.text, body.idempotency_key)
        return entry.model_dump(mode="json")

    @router.get("/tasks/{task_id}/events")
    async def events(task_id: UUID, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        messages = await coordinator.events.history(task_id, after_seq=after_seq, limit=limit)
        return [message.model_dump(mode="json") for message in messages]

    @router.get("/tasks/{task_id}/events/stream")
    async def event_stream(task_id: UUID, request: Request, after_seq: int = 0) -> StreamingResponse:
        async def stream():
            cursor = after_seq
            while not await request.is_disconnected():
                messages = await coordinator.events.history(task_id, after_seq=cursor, limit=200)
                for message in messages:
                    cursor = message.seq
                    yield f"id: {cursor}\ndata: {message.model_dump_json()}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.post("/tasks/{task_id}/agents/{agent_id}/pause")
    async def pause_agent(task_id: UUID, agent_id: str) -> dict[str, Any]:
        return (await coordinator.pause_agent(task_id, agent_id)).model_dump(mode="json")

    @router.post("/tasks/{task_id}/agents/{agent_id}/resume")
    async def resume_agent(task_id: UUID, agent_id: str) -> dict[str, Any]:
        return (await coordinator.resume_agent(task_id, agent_id)).model_dump(mode="json")

    @router.post("/tasks/{task_id}/agents/{agent_id}/model")
    async def set_model(task_id: UUID, agent_id: str, body: ModelRequest) -> dict[str, Any]:
        return (
            await coordinator.set_agent_model(task_id, agent_id, body.provider_id, body.model_id, body.protocol)
        ).model_dump(mode="json")

    @router.post("/tasks/{task_id}/stop", status_code=204)
    async def stop_task(task_id: UUID) -> None:
        await automation.cancel(task_id)
        await coordinator.stop_task(task_id)

    @router.post("/tasks/{task_id}/restart")
    async def restart_task(task_id: UUID, body: RestartTaskRequest | None = None) -> dict[str, Any]:
        payload = body or RestartTaskRequest()
        overrides = (
            {
                agent_id: (model.provider_id, model.model_id, model.protocol, model.runtime)
                for agent_id, model in payload.agent_models.items()
            }
            if payload.agent_models is not None
            else None
        )
        return await coordinator.restart_task(
            task_id,
            rerun_scout=payload.rerun_scout,
            retry_abandoned=payload.retry_abandoned,
            model_overrides=overrides,
            aoe_first_attempt_minutes=payload.aoe_first_attempt_minutes,
            aoe_second_attempt_minutes=payload.aoe_second_attempt_minutes,
            aoe_joint_minutes=payload.aoe_joint_minutes,
        )

    @router.get("/tasks/{task_id}/clone-config")
    async def clone_task_config(task_id: UUID) -> dict[str, Any]:
        return await coordinator.clone_config(task_id)

    @router.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: UUID) -> None:
        await automation.cancel(task_id)
        await coordinator.delete_task(task_id)
        await asyncio.to_thread(automation.experience.delete_task, task_id)

    @router.get("/tasks/{task_id}/writeup")
    async def writeup(task_id: UUID) -> dict[str, Any]:
        return (await storage.latest_writeup(task_id)).model_dump(mode="json")

    @router.get("/tasks/{task_id}/writeup/download")
    async def download_writeup(task_id: UUID) -> FileResponse:
        item = await storage.latest_writeup(task_id)
        return FileResponse(item.storage_path, media_type="text/markdown", filename="writeup.md")

    return router
