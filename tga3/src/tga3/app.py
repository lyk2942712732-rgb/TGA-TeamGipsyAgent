"""TGA3 control-plane HTTP/WebSocket/MCP application."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import TGA3Config
from .coordinator import TaskCoordinator
from .docker_runtime import ContainerRuntime
from .domain import USER_ACTOR, EntryKind, InputFile, PublishRequest, RunState, UserFileBody
from .errors import TGA3Error
from .host_agents import Automation, HostModel, OpenAIHostModel
from .mcp_server import build_mcp
from .skills import SkillCatalog
from .storage import Storage


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1)


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    addressed_to: list[str] = Field(default_factory=list)
    attachment_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()))


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()))


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    model_id: str


def create_app(
    config: TGA3Config,
    storage: Storage,
    containers: ContainerRuntime,
    *,
    host_model: HostModel | None = None,
) -> FastAPI:
    config.ensure_directories()
    skills = SkillCatalog(config.skills_root)
    coordinator = TaskCoordinator(config, storage, containers)
    automation = Automation(
        config,
        storage,
        coordinator.blackboard,
        coordinator.dialogue,
        host_model or OpenAIHostModel(config, skills),
        coordinator.finish_task_workers,
    )
    coordinator.change_handlers.append(automation.changed)
    mcp = build_mcp(config, coordinator.blackboard, skills)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="TGA3 Control Plane", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.storage = storage
    app.state.coordinator = coordinator
    app.state.automation = automation
    api = APIRouter(prefix="/api/v3")

    @app.exception_handler(TGA3Error)
    async def tga3_error(_request: Request, exc: TGA3Error) -> JSONResponse:
        status = 404 if exc.code == "not_found" else 409 if exc.code == "conflict" else 422
        return JSONResponse(status_code=status, content={"error": exc.code, "message": str(exc)})

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "tga3"}

    @api.get("/tasks")
    async def list_tasks(limit: int = 200) -> list[dict[str, Any]]:
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        return [task.model_dump(mode="json") for task in tasks]

    @api.post("/tasks")
    async def create_task(body: CreateTaskRequest) -> dict[str, Any]:
        task = await coordinator.create_and_start(body.title, body.prompt)
        return task.model_dump(mode="json")

    @api.get("/tasks/{task_id}")
    async def get_task(task_id: UUID) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        agents = await storage.list_agents(task_id)
        return {
            "task": task.model_dump(mode="json"),
            "agents": [item.model_dump(mode="json") for item in agents],
        }

    @api.post("/tasks/{task_id}/files")
    async def upload_file(task_id: UUID, file: UploadFile) -> dict[str, Any]:
        await storage.get_task(task_id)
        name = Path(file.filename or "upload.bin").name
        item_id = uuid4()
        root = config.resolve_path(config.runtime.input_root) / str(task_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{item_id}-{name}"
        content = await file.read()
        path.write_bytes(content)
        item = InputFile(
            id=item_id,
            task_id=task_id,
            name=name,
            storage_path=str(path),
            media_type=file.content_type or "application/octet-stream",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        await storage.register_input_file(item)
        body = UserFileBody(
            input_file_id=item.id,
            name=item.name,
            media_type=item.media_type,
            sha256=item.sha256,
        )
        entry = await coordinator.blackboard.publish(
            task_id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_FILE,
                topic="input",
                body=body.model_dump(mode="json"),
                idempotency_key=f"file-{item.id}",
            ),
        )
        return {"file": item.model_dump(mode="json"), "blackboard_entry_id": str(entry.id)}

    @api.post("/tasks/{task_id}/prompts")
    async def add_prompt(task_id: UUID, body: PromptRequest) -> dict[str, Any]:
        entry = await coordinator.add_prompt(
            task_id,
            body.text,
            addressed_to=body.addressed_to,
            attachment_ids=body.attachment_ids,
            idempotency_key=body.idempotency_key,
        )
        for agent_id in body.addressed_to:
            await coordinator.gateway.notify(task_id, agent_id, "session.add_prompt", {"text": body.text})
        return entry.model_dump(mode="json")

    @api.get("/tasks/{task_id}/blackboard")
    async def blackboard(task_id: UUID, after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        return (await coordinator.blackboard.sync(task_id, after_seq=after_seq, limit=limit)).model_dump(mode="json")

    @api.get("/tasks/{task_id}/dialogue")
    async def dialogue(task_id: UUID, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        messages = await coordinator.dialogue.history(task_id, after_seq=after_seq, limit=limit)
        return [message.model_dump(mode="json") for message in messages]

    @api.get("/tasks/{task_id}/dialogue/stream")
    async def dialogue_stream(task_id: UUID, request: Request, after_seq: int = 0) -> StreamingResponse:
        async def events():
            cursor = after_seq
            while not await request.is_disconnected():
                messages = await coordinator.dialogue.history(task_id, after_seq=cursor, limit=200)
                for message in messages:
                    cursor = message.seq
                    yield f"id: {cursor}\ndata: {message.model_dump_json()}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream")

    @api.post("/questions/{question_id}/answer")
    async def answer_question(question_id: UUID, body: AnswerRequest) -> dict[str, Any]:
        entry = await coordinator.dialogue.answer(
            question_id,
            answer=body.answer,
            attachment_ids=body.attachment_ids,
            idempotency_key=body.idempotency_key,
        )
        await coordinator.blackboard_changed(entry.task_id, entry.seq)
        await storage.update_task(entry.task_id, state=RunState.RUNNING)
        return entry.model_dump(mode="json")

    @api.post("/tasks/{task_id}/agents/{agent_id}/pause")
    async def pause_agent(task_id: UUID, agent_id: str) -> dict[str, Any]:
        return (await coordinator.pause_agent(task_id, agent_id)).model_dump(mode="json")

    @api.post("/tasks/{task_id}/agents/{agent_id}/resume")
    async def resume_agent(task_id: UUID, agent_id: str) -> dict[str, Any]:
        return (await coordinator.resume_agent(task_id, agent_id)).model_dump(mode="json")

    @api.post("/tasks/{task_id}/agents/{agent_id}/model")
    async def set_model(task_id: UUID, agent_id: str, body: ModelRequest) -> dict[str, Any]:
        return (await coordinator.set_agent_model(task_id, agent_id, body.provider_id, body.model_id)).model_dump(
            mode="json"
        )

    @api.post("/tasks/{task_id}/stop", status_code=204)
    async def stop_task(task_id: UUID) -> None:
        await coordinator.stop_task(task_id)

    @api.get("/models")
    async def models() -> dict[str, Any]:
        return {
            "providers": [
                {
                    "id": provider.id,
                    "name": provider.name,
                    "protocol": provider.protocol,
                    "models": [model.model_dump(mode="json") for model in provider.models],
                }
                for provider in config.models.providers
            ],
            "bindings": {
                agent_id: binding.model_dump(mode="json")
                for agent_id, binding in config.agent_models.agents.items()
            },
        }

    @api.get("/skills")
    async def skill_index() -> list[dict[str, str]]:
        return [{"name": item.name, "description": item.description} for item in skills.list()]

    @api.get("/skills/{name}")
    async def skill_document(name: str) -> dict[str, str]:
        return {"name": name, "content": skills.read(name)}

    @api.get("/tasks/{task_id}/writeup")
    async def writeup(task_id: UUID) -> dict[str, Any]:
        return (await storage.latest_writeup(task_id)).model_dump(mode="json")

    @api.get("/tasks/{task_id}/writeup/download")
    async def download_writeup(task_id: UUID) -> FileResponse:
        item = await storage.latest_writeup(task_id)
        return FileResponse(item.storage_path, media_type="text/markdown", filename="writeup.md")

    @app.websocket("/internal/agents/{task_id}/{agent_id}")
    async def agent_socket(websocket: WebSocket, task_id: UUID, agent_id: str) -> None:
        await coordinator.gateway.attach(task_id, agent_id, websocket)

    app.include_router(api)
    app.mount("/mcp", mcp.streamable_http_app())
    return app


__all__ = ["create_app"]
