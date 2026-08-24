"""TGA3 control-plane HTTP/WebSocket/MCP application."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import AgentsConfig, ConfigBundle, ModelConfig, ModelsConfig, RuntimeConfig, ScenesConfig, TGA3Config
from .coordinator import TaskCoordinator
from .docker_runtime import ContainerRuntime
from .domain import AgentState, DialogueKind, RunState
from .errors import TGA3Error
from .host_agents import Automation, HostModel, OpenAIHostModel
from .mcp_server import build_mcp
from .model_discovery import discover_provider_models
from .provider_profiles import PROVIDER_PROFILES, ProviderProtocol
from .skills import SkillCatalog
from .storage import Storage


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
    protocol: ProviderProtocol


class SkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1)


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
    config_lock = asyncio.Lock()

    @app.exception_handler(TGA3Error)
    async def tga3_error(_request: Request, exc: TGA3Error) -> JSONResponse:
        status = 404 if exc.code == "not_found" else 409 if exc.code == "conflict" else 422
        return JSONResponse(status_code=status, content={"error": exc.code, "message": str(exc)})

    @app.exception_handler(KeyError)
    @app.exception_handler(ValueError)
    async def configuration_error(_request: Request, exc: KeyError | ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_configuration", "message": str(exc)})

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "tga3"}

    @api.get("/tasks")
    async def list_tasks(limit: int = 200) -> list[dict[str, Any]]:
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        return [task.model_dump(mode="json") for task in tasks]

    @api.get("/attention")
    async def attention(limit: int = 200) -> list[dict[str, Any]]:
        """Human-action queue for questions, pauses and failures."""

        result: list[dict[str, Any]] = []
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        for task in tasks:
            if task.state == RunState.WAITING_USER:
                messages = await coordinator.dialogue.history(task.id, after_seq=0, limit=500)
                for message in reversed(messages):
                    if message.kind != DialogueKind.QUESTION or not message.payload.get("question_id"):
                        continue
                    question = await storage.get_question(UUID(str(message.payload["question_id"])))
                    if question.state == "waiting":
                        result.append({
                            "id": f"question:{question.id}", "kind": "question", "task_id": str(task.id),
                            "task_title": task.title, "task_state": task.state, "agent_id": question.origin.agent_id,
                            "title": "等待用户回答", "detail": question.question, "question_id": str(question.id),
                            "created_at": question.created_at,
                        })
                        break
            if task.state == RunState.FAILED:
                result.append({
                    "id": f"task:{task.id}:failed", "kind": "task_failed", "task_id": str(task.id),
                    "task_title": task.title, "task_state": task.state, "agent_id": None,
                    "title": "任务运行失败", "detail": "打开任务查看错误和 Agent 状态。", "question_id": None,
                    "created_at": task.updated_at,
                })
            for agent in await storage.list_agents(task.id):
                if agent.actual_state not in {AgentState.PAUSED, AgentState.PAUSE_REQUESTED, AgentState.FAILED}:
                    continue
                result.append({
                    "id": f"agent:{task.id}:{agent.agent_id}:{agent.actual_state}",
                    "kind": "agent_failed" if agent.actual_state == AgentState.FAILED else "agent_paused",
                    "task_id": str(task.id), "task_title": task.title, "task_state": task.state,
                    "agent_id": agent.agent_id,
                    "title": "Agent 运行失败" if agent.actual_state == AgentState.FAILED else "Agent 已暂停",
                    "detail": agent.last_error or ("可恢复该 Agent，或打开任务补充提示。"),
                    "question_id": None, "created_at": agent.updated_at,
                })
        return sorted(result, key=lambda item: str(item["created_at"]), reverse=True)

    @api.post("/tasks")
    async def create_task(
        title: Annotated[str, Form(min_length=1, max_length=500)],
        prompt: Annotated[str, Form(min_length=1)],
        scene_id: Annotated[str, Form(min_length=1, max_length=100)],
        agent_models: Annotated[str, Form()] = "{}",
        files: Annotated[list[UploadFile] | None, File()] = None,
    ) -> dict[str, Any]:
        raw_overrides = json.loads(agent_models)
        if not isinstance(raw_overrides, dict):
            raise ValueError("agent_models must be an object")
        overrides = {
            agent_id: ModelRequest.model_validate(value)
            for agent_id, value in raw_overrides.items()
        }
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
                agent_id: (value.provider_id, value.model_id, value.protocol)
                for agent_id, value in overrides.items()
            },
        )
        return task.model_dump(mode="json")

    @api.get("/tasks/{task_id}")
    async def get_task(task_id: UUID) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        agents = await storage.list_agents(task_id)
        return {
            "task": task.model_dump(mode="json"),
            "agents": [
                {
                    **item.model_dump(mode="json"),
                    "display_name": config.agents.agents[item.agent_id].display_name,
                    "role": config.agents.agents[item.agent_id].role,
                    "model_name": config.models.provider(item.provider_id).model(item.model_id).name,
                    "runtime_location": (
                        "container" if config.agents.agents[item.agent_id].role == "worker" else "host"
                    ),
                }
                for item in agents
            ],
        }

    @api.post("/tasks/{task_id}/files")
    async def upload_file(task_id: UUID, file: UploadFile) -> dict[str, Any]:
        item, entry_id = await coordinator.add_input_file(
            task_id,
            file.filename or "upload.bin",
            file.content_type or "application/octet-stream",
            await file.read(),
        )
        return {"file": item.model_dump(mode="json"), "blackboard_entry_id": str(entry_id)}

    @api.post("/tasks/{task_id}/prompts")
    async def add_prompt(task_id: UUID, body: PromptRequest) -> dict[str, Any]:
        entry = await coordinator.add_prompt(
            task_id,
            body.text,
            addressed_to=body.addressed_to,
            attachment_ids=body.attachment_ids,
            idempotency_key=body.idempotency_key,
        )
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
        return (
            await coordinator.set_agent_model(
                task_id, agent_id, body.provider_id, body.model_id, body.protocol
            )
        ).model_dump(mode="json")

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
                    "protocols": provider.protocols,
                    "models": [model.model_dump(mode="json") for model in provider.models],
                }
                for provider in config.models.providers
            ],
            "bindings": {
                agent_id: {
                    "display_name": binding.display_name,
                    "role": binding.role,
                    "runtime": binding.runtime,
                    "provider_id": binding.provider_id,
                    "model_id": binding.model_id,
                    "protocol": binding.protocol,
                    "max_turns_per_cycle": binding.max_turns_per_cycle,
                }
                for agent_id, binding in config.agents.agents.items()
            },
        }

    @api.get("/agent-definitions")
    async def agent_definitions() -> list[dict[str, Any]]:
        tools = {
            "supervisor": ["skills.list", "skills.read"],
            "worker-openai": [
                "shell_exec", "progress_update", "blackboard.sync", "blackboard.publish",
                "artifact.register", "skills.list", "skills.read",
            ],
            "worker-claude": [
                "Bash", "Read", "Write", "Edit", "Glob", "Grep", "blackboard.sync",
                "blackboard.publish", "artifact.register", "skills.list", "skills.read",
            ],
            "reporter": ["skills.list", "skills.read"],
        }
        result: list[dict[str, Any]] = []
        for agent_id, binding in config.agents.agents.items():
            image = config.runtime.worker_images.get(agent_id)
            image_health = await containers.image_status(image) if image else None
            result.append({
                "id": agent_id,
                **binding.model_dump(mode="json"),
                "tools": tools.get(agent_id, []),
                "image": image,
                "image_health": image_health,
            })
        return result

    @api.get("/config/models")
    async def read_models_config() -> dict:
        return config.export_document("models")

    @api.get("/config/model-provider-presets")
    async def read_model_provider_presets() -> list[dict[str, object]]:
        return [profile.public_dict() for profile in PROVIDER_PROFILES]

    @api.put("/config/models")
    async def write_models_config(body: ModelsConfig) -> dict:
        async with config_lock:
            config.apply_document("models", body)
        return config.export_document("models")

    @api.delete("/config/models/{provider_id}", status_code=204)
    async def delete_model_provider(provider_id: str) -> None:
        async with config_lock:
            updated = config.models.model_copy(deep=True)
            provider = updated.provider(provider_id)
            if provider.built_in:
                raise ValueError("内置供应商不能删除；可以不配置它的 API 密钥")
            bound_agents = [
                agent_id
                for agent_id, binding in config.agents.agents.items()
                if binding.provider_id == provider_id
            ]
            if bound_agents:
                raise ValueError(f"供应商仍被 Agent 使用：{', '.join(bound_agents)}")
            updated.providers = [item for item in updated.providers if item.id != provider_id]
            config.apply_document("models", updated)

    @api.post("/config/models/{provider_id}/discover")
    async def discover_models(provider_id: str) -> dict[str, Any]:
        snapshot = config.models.provider(provider_id).model_copy(deep=True)
        discovered = await discover_provider_models(snapshot)
        async with config_lock:
            updated = config.models.model_copy(deep=True)
            provider = updated.provider(provider_id)
            if (
                provider.base_url != snapshot.base_url
                or provider.selected_api_key_id != snapshot.selected_api_key_id
            ):
                raise ValueError("供应商 URL 或所选密钥已变化，请重新同步")
            existing_by_name = {model.name: model for model in provider.models}
            existing_by_id = {model.id: model for model in provider.models}
            provider.models = [
                (
                    existing_by_name.get(remote_id)
                    or existing_by_id.get(remote_id)
                    or ModelConfig(id=remote_id, name=remote_id)
                )
                .model_copy(update={"name": remote_id, "verification_status": "verified", "source": "api"})
                for remote_id in discovered
            ]
            discovered_ids = {model.id for model in provider.models}
            updated_agents = config.agents.model_copy(deep=True)
            rebound_agents: list[str] = []
            for agent_id, binding in updated_agents.agents.items():
                if binding.provider_id == provider_id and binding.model_id not in discovered_ids:
                    binding.model_id = provider.models[0].id
                    rebound_agents.append(agent_id)
            config.apply_bundle(
                ConfigBundle(
                    models=updated,
                    agents=updated_agents,
                    scenes=config.scenes,
                    runtime=config.runtime,
                )
            )
        return {
            "provider_id": provider_id,
            "count": len(provider.models),
            "models": [model.model_dump(mode="json") for model in provider.models],
            "rebound_agents": rebound_agents,
        }

    @api.get("/config/agents")
    async def read_agents_config() -> dict:
        return config.export_document("agents")

    @api.put("/config/agents")
    async def write_agents_config(body: AgentsConfig) -> dict:
        async with config_lock:
            config.apply_document("agents", body)
        return config.export_document("agents")

    @api.get("/config/scenes")
    async def read_scenes_config() -> dict:
        return config.export_document("scenes")

    @api.put("/config/scenes")
    async def write_scenes_config(body: ScenesConfig) -> dict:
        async with config_lock:
            config.apply_document("scenes", body)
        return config.export_document("scenes")

    @api.get("/config/runtime")
    async def read_runtime_config() -> dict:
        return config.export_document("runtime")

    @api.put("/config/runtime")
    async def write_runtime_config(body: RuntimeConfig) -> dict:
        async with config_lock:
            config.apply_document("runtime", body)
            skills.set_root(config.skills_root)
        return config.export_document("runtime")

    @api.get("/scenes")
    async def scenes() -> list[dict[str, str]]:
        return [
            {"id": scene.id, "name": scene.name, "description": scene.description}
            for scene in config.scenes.scenes
        ]

    @api.get("/skills")
    async def skill_index() -> list[dict[str, str]]:
        return [{"name": item.name, "description": item.description} for item in skills.list()]

    @api.get("/skills/{name}")
    async def skill_document(name: str) -> dict[str, str]:
        return {"name": name, "content": skills.read(name)}

    @api.put("/skills/{name}")
    async def write_skill(name: str, body: SkillRequest) -> dict[str, str]:
        item = skills.write(name, body.content)
        return {"name": item.name, "description": item.description, "content": skills.read(name)}

    @api.delete("/skills/{name}", status_code=204)
    async def delete_skill(name: str) -> None:
        skills.delete(name)

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
