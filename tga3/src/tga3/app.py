"""TGA3 control-plane HTTP/WebSocket/MCP application."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .blackboard_delivery import BlackboardDelivery
from .config import AgentsConfig, ModelConfig, ModelsConfig, RuntimeConfig, ScenesConfig, TGA3Config
from .coordinator import TaskCoordinator
from .docker_runtime import ContainerRuntime
from .domain import USER_ACTOR, AgentState, PublishRequest, RunState
from .errors import TGA3Error
from .file_publications import FilePublications
from .host_agents import Automation, CliHostModel, HostModel
from .knowledge_relay import KnowledgeRelay
from .knowledge import KnowledgeSource
from .local_knowledge import LocalKnowledge, build_local_knowledge_mcp
from .mcp_server import build_scout_mcp, build_web_mcp
from .model_discovery import discover_provider_models
from .provider_profiles import PROVIDER_PROFILES, ProviderProtocol
from .skills import MAX_ARCHIVE_BYTES, SkillCatalog
from .storage import Storage
from .token_usage import COUNTERS, TokenUsageTracker
from .web_search import WebSearchService


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    model_id: str
    protocol: ProviderProtocol


class ModelEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(min_length=1)
    enabled: bool


class SkillPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: dict[str, str] = Field(min_length=1)


class RealtimePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=20000)
    idempotency_key: str = Field(min_length=1, max_length=180)


class ScoutSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int = Field(ge=60, le=600)


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
        coordinator.events,
        host_model or CliHostModel(config, containers, storage, coordinator.events),
        coordinator.finish_task_workers,
    )
    coordinator.change_handlers.append(automation.changed)
    coordinator.aoe.automation = automation
    scout_mcp = build_scout_mcp(config, coordinator.scout_intel)
    aoe_scout_mcp = build_scout_mcp(config, coordinator.scout_intel, aoe=coordinator.aoe)
    web_search = WebSearchService(config, storage)
    knowledge_relay = KnowledgeRelay(config)
    local_knowledge = LocalKnowledge(config)
    local_knowledge_mcp = build_local_knowledge_mcp(config, local_knowledge)
    web_mcp = build_web_mcp(config, web_search)
    delivery = BlackboardDelivery(config, storage, coordinator.blackboard)
    usage = TokenUsageTracker(config, storage)
    publications = FilePublications(config, coordinator.blackboard)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with (scout_mcp.session_manager.run(), aoe_scout_mcp.session_manager.run(),
                    web_mcp.session_manager.run(), local_knowledge_mcp.session_manager.run()):
            await coordinator.restore_aoe()
            async def clean_target_leases():
                while True:
                    parents = {v["parent_id"] for v in coordinator.aoe.targets.records.values() if v.get("owned")}
                    for parent in parents:
                        try:
                            await coordinator.aoe.targets.reconcile(await storage.list_questions(UUID(parent)),
                                                                   parent_id=UUID(parent))
                        except Exception as exc:
                            # A transient database/network failure must not permanently stop cleanup.
                            import logging

                            logging.getLogger(__name__).warning("Target cleanup will retry: %s", type(exc).__name__)
                    await asyncio.sleep(5)

            target_cleanup = asyncio.create_task(clean_target_leases())
            try:
                yield
            finally:
                target_cleanup.cancel()
                await asyncio.gather(target_cleanup, return_exceptions=True)
                await coordinator.close()
                await web_search.close()
                await knowledge_relay.close()

    app = FastAPI(title="TGA3 Control Plane", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.storage = storage
    app.state.coordinator = coordinator
    app.state.automation = automation
    app.state.publications = publications
    app.state.web_search = web_search
    app.state.knowledge_relay = knowledge_relay
    app.state.experience = automation.experience
    api = APIRouter(prefix="/api/v3")
    config_lock = asyncio.Lock()

    @app.api_route("/knowledge-mcp/", methods=["GET", "POST", "DELETE"])
    async def knowledge_mcp(request: Request):
        return await knowledge_relay.forward(request)

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
        """Runtime attention queue for pauses and failures."""

        result: list[dict[str, Any]] = []
        tasks = await storage.list_tasks(limit=min(max(limit, 1), 500))
        for task in tasks:
            if task.state == RunState.FAILED:
                result.append({
                    "id": f"task:{task.id}:failed", "kind": "task_failed", "task_id": str(task.id),
                    "task_title": task.title, "task_state": task.state, "agent_id": None,
                    "title": "任务运行失败", "detail": "打开任务查看错误和 Agent 状态。",
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
                    "created_at": agent.updated_at,
                })
        return sorted(result, key=lambda item: str(item["created_at"]), reverse=True)

    @api.post("/tasks")
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
            aoe_attempt_minutes=aoe_attempt_minutes,
            aoe_first_attempt_minutes=aoe_first_attempt_minutes,
            aoe_second_attempt_minutes=aoe_second_attempt_minutes,
            aoe_joint_minutes=aoe_joint_minutes,
            aoe_auto_submit_flag=aoe_auto_submit_flag,
            knowledge_source=knowledge_source,
        )
        return task.model_dump(mode="json")

    @api.get("/tasks/{task_id}")
    async def get_task(task_id: UUID) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        await usage.refresh_task(task_id)
        if task.scene_id == "aoe" and task.parent_task_id is None:
            totals = {}
            for question in await storage.list_questions(task_id):
                await usage.refresh_task(question.id)
                for agent in await storage.list_agents(question.id):
                    if agent.agent_id == "scout":
                        continue  # Scout has one shared native session under the parent workspace.
                    current = agent.metadata.get("token_usage")
                    if current and current.get("reported"):
                        value = totals.setdefault(agent.agent_id, {key: 0 for key in COUNTERS})
                        for key in COUNTERS:
                            value[key] += current.get(key, 0)
            for agent_id, total in totals.items():
                current = await storage.get_agent(task_id, agent_id)
                if any(current.metadata.get("token_usage", {}).get(key) != total[key] for key in COUNTERS):
                    await storage.update_agent(task_id, agent_id,
                        metadata={"token_usage": {
                            **total, "reported": True, "updated_at": task.updated_at.isoformat(),
                        }})
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
                    "metadata": {k: v for k, v in item.metadata.items()
                                 if k not in {"token_usage_sources", "intel_drafts", "intel_draft_order"}},
                    "display_name": config.agents.agents[item.agent_id].display_name,
                    "role": config.agents.agents[item.agent_id].role,
                    "model_name": historical_model_name(item),
                    "runtime_location": (
                        "container" if config.agents.agents[item.agent_id].role in {"scout", "worker", "reporter"}
                        else "reserved"
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


    @api.get("/tasks/{task_id}/blackboard")
    async def blackboard(task_id: UUID, after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        return (await coordinator.blackboard.sync(task_id, after_seq=after_seq, limit=limit)).model_dump(mode="json")

    @api.get("/tasks/{task_id}/questions")
    async def question_overview(task_id: UUID) -> dict:
        return await coordinator.aoe.snapshot(task_id)

    async def owned_question(task_id: UUID, question_id: UUID):
        question = await storage.get_task(question_id)
        if question.parent_task_id != task_id:
            raise ValueError("题目不属于当前 AOE 任务")
        return question

    @api.get("/tasks/{task_id}/experience")
    async def experience_cards(task_id: UUID) -> dict:
        await storage.get_task(task_id)
        cards, states = await asyncio.gather(
            asyncio.to_thread(automation.experience.cards, task_id),
            asyncio.to_thread(automation.experience.states, task_id),
        )
        return {"cards": cards, "state": states}

    @api.get("/tasks/{task_id}/experience/{card_id}/resources/{relative:path}")
    async def experience_resource(task_id: UUID, card_id: str, relative: str) -> FileResponse:
        await storage.get_task(task_id)
        path = await asyncio.to_thread(automation.experience.resource, task_id, card_id, relative)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @api.get("/tasks/{task_id}/questions/{question_id}/files/{file_id}")
    async def question_file(task_id: UUID, question_id: UUID, file_id: UUID):
        await owned_question(task_id, question_id)
        item = next((file for file in await storage.list_input_files(question_id) if file.id == file_id), None)
        if item is None:
            raise KeyError("题目附件不存在")
        from pathlib import Path

        path = Path(item.storage_path)
        if path.is_symlink() or not path.resolve().is_relative_to(config.task_path(question_id, "inputs").resolve()):
            raise ValueError("题目附件路径不可用")
        return FileResponse(path, filename=item.name, media_type=item.media_type)

    @api.get("/tasks/{task_id}/aoe/events")
    async def question_events(task_id: UUID, question_id: UUID | None = None,
                              agent_id: str | None = None, limit: int = 500) -> dict:
        parent = await storage.get_task(task_id)
        if parent.scene_id != "aoe" or parent.parent_task_id is not None:
            raise ValueError("当前任务不是 AOE 批次")
        limit = min(max(limit, 1), 10000)
        questions = ([await owned_question(task_id, question_id)] if question_id
                     else await storage.list_questions(task_id))
        result = []
        has_more = False
        for target in [parent, *questions]:
            has_more |= target.event_seq > limit
            messages = await storage.list_events(target.id, after_seq=max(0, target.event_seq - limit), limit=limit)
            for message in messages:
                identity = message.question_id or message.payload.get("question_id")
                if question_id and str(identity) != str(question_id):
                    continue
                if agent_id and not (message.channel_agent_id == agent_id or message.actor.agent_id == agent_id
                                     or message.payload.get("agent_id") == agent_id):
                    continue
                result.append(message)
        result.sort(key=lambda item: (item.created_at, str(item.id)))
        has_more |= len(result) > limit
        return {"events": [item.model_dump(mode="json") for item in result[-limit:]], "has_more": has_more}

    @api.post("/tasks/{task_id}/blackboard/prompts")
    async def realtime_prompt(task_id: UUID, body: RealtimePromptRequest) -> dict[str, Any]:
        task = await storage.get_task(task_id)
        if task.state not in {RunState.SCOUTING, RunState.RUNNING}:
            raise ValueError("当前任务已停止接收实时提示")
        entry = await coordinator.blackboard.publish(
            task_id, USER_ACTOR,
            PublishRequest(kind="user_prompt", topic="realtime", body={"text": body.text},
                           idempotency_key=f"realtime:{body.idempotency_key}"),
        )
        if task.scene_id == "aoe" and task.parent_task_id is None:
            for question in await storage.list_questions(task_id):
                if question.state in {RunState.SCOUTING, RunState.RUNNING}:
                    await coordinator.blackboard.publish(question.id, USER_ACTOR, PublishRequest(
                        kind="user_prompt", topic="realtime", body={"text": body.text},
                        idempotency_key=f"aoe-realtime:{body.idempotency_key}"))
        return entry.model_dump(mode="json")

    @api.get("/tasks/{task_id}/events")
    async def events(task_id: UUID, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        messages = await coordinator.events.history(task_id, after_seq=after_seq, limit=limit)
        return [message.model_dump(mode="json") for message in messages]

    @api.get("/tasks/{task_id}/events/stream")
    async def event_stream(task_id: UUID, request: Request, after_seq: int = 0) -> StreamingResponse:
        async def events():
            cursor = after_seq
            while not await request.is_disconnected():
                messages = await coordinator.events.history(task_id, after_seq=cursor, limit=200)
                for message in messages:
                    cursor = message.seq
                    yield f"id: {cursor}\ndata: {message.model_dump_json()}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream")


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
        await automation.cancel(task_id)
        await coordinator.stop_task(task_id)

    @api.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: UUID) -> None:
        await automation.cancel(task_id)
        await coordinator.delete_task(task_id)
        await asyncio.to_thread(automation.experience.delete_task, task_id)

    @api.get("/models")
    async def models() -> dict[str, Any]:
        return {
            "providers": [
                {
                    "id": provider.id,
                    "name": provider.name,
                    "endpoints": {
                        protocol: endpoint.model_dump(mode="json")
                        for protocol, endpoint in provider.endpoints.items()
                    },
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
                }
                for agent_id, binding in config.agents.agents.items()
            },
        }

    @api.get("/agent-definitions")
    async def agent_definitions() -> list[dict[str, Any]]:
        tools = {
            "scout": ["第一轮：Shell / 安全工具 / logs 留存", "第二轮：只读 logs",
                      "scout_write_intel → /intels/raw/scout-intels.json", "第三轮：Intel 合并",
                      "scout_merge_intel → /intels/merged/scout-intels.json", "系统仅导入合并 Intel"],
            "worker-openai": [
                "Shell", "Read", "Edit", "web_search", "原生黑板同步 Hook", "/findings/ 独立 JSON", "系统导入最终候选文件",
                "按 action 自动关联复现方法和证据", "发布时自动校验证据路径",
            ],
            "worker-claude": [
                "Bash", "Read", "Write", "Edit", "Glob", "Grep", "web_search", "WebSearch", "WebFetch", "原生黑板同步 Hook",
                "/findings/ 独立 JSON", "系统导入最终候选文件", "按 action 自动关联复现方法和证据", "发布时自动校验证据路径",
            ],
            "reporter": ["Read snapshot", "Read evidence", "Read skills", "Markdown Writeup"],
        }
        for name, native in [('worker-opencode', ['bash', 'read', 'write', 'edit']),
                             ('worker-qwen', ['run_shell_command', 'read_file', 'write_file', 'edit',
                                              'tool_search', 'tool_call'])]:
            tools[name] = [*native, 'web_search', '知识库 MCP', 'Skills', '经验卡', '原生黑板同步 Hook',
                           '/findings/ 独立 JSON', '系统导入最终候选文件', '按 action 自动关联复现方法和证据']
        result: list[dict[str, Any]] = []
        for agent_id, binding in config.agents.agents.items():
            image = config.runtime.worker_images.get(agent_id)
            image_health = await containers.image_status(image) if image else None
            result.append({
                "id": agent_id,
                "scout_timeout_seconds": config.runtime.scout_timeout_seconds if binding.role == "scout" else None,
                "scout_extract_timeout_seconds": None,
                **binding.model_dump(mode="json"),
                "tools": tools.get(agent_id, []),
                "image": image,
                "image_health": image_health,
            })
        return result

    @api.get("/config/models")
    async def read_models_config() -> dict:
        return config.export_document("models")

    @api.put("/config/scout-settings")
    async def write_scout_settings(body: ScoutSettingsRequest) -> dict:
        async with config_lock:
            updated = config.runtime.model_copy(update={"scout_timeout_seconds": body.timeout_seconds})
            config.apply_document("runtime", updated)
        return {"scout_timeout_seconds": config.runtime.scout_timeout_seconds}

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
    async def discover_models(provider_id: str, protocol: ProviderProtocol) -> dict[str, Any]:
        snapshot = config.models.provider(provider_id).model_copy(deep=True)
        discovered = await discover_provider_models(snapshot, protocol)
        async with config_lock:
            updated = config.models.model_copy(deep=True)
            provider = updated.provider(provider_id)
            if (
                provider.endpoint(protocol).base_url != snapshot.endpoint(protocol).base_url
                or provider.selected_api_key_id != snapshot.selected_api_key_id
                or provider.key() != snapshot.key()
            ):
                raise ValueError("供应商 URL 或所选密钥已变化，请重新同步")
            endpoint = provider.endpoint(protocol)
            existing_by_name = {model.name: model for model in endpoint.models}
            existing_by_id = {model.id: model for model in endpoint.models}
            refreshed = [
                (
                    existing_by_name.get(remote_id)
                    or existing_by_id.get(remote_id)
                    or ModelConfig(id=remote_id, name=remote_id, enabled=False)
                )
                .model_copy(update={"name": remote_id, "available": True, "source": "api"})
                for remote_id in discovered
            ]
            discovered_ids = {model.id for model in refreshed}
            # Keep unavailable records so existing bindings remain inspectable.
            endpoint.models = refreshed + [
                model.model_copy(update={"enabled": False, "available": False})
                for model in endpoint.models if model.id not in discovered_ids
            ]
            config.apply_document("models", updated)
        return {
            "provider_id": provider_id,
            "protocol": protocol,
            "count": len(discovered),
            "models": [model.model_dump(mode="json") for model in endpoint.models],
        }

    @api.patch("/config/models/{provider_id}/endpoints/{protocol}/models")
    async def set_models_enabled(
        provider_id: str, protocol: ProviderProtocol, body: ModelEnabledRequest
    ) -> dict[str, Any]:
        async with config_lock:
            updated = config.models.model_copy(deep=True)
            provider = updated.provider(provider_id)
            selected = [provider.model(model_id, protocol) for model_id in set(body.model_ids)]
            if body.enabled and any(not model.available for model in selected):
                raise ValueError("模型已不在此端点的可访问列表中，请先重新读取模型")
            for model in selected:
                model.enabled = body.enabled
            config.apply_document("models", updated)
        return {"provider_id": provider_id, "protocol": protocol, "enabled": body.enabled}

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
    async def skill_index() -> list[dict[str, Any]]:
        return [
            {"name": item.name, "description": item.description, "file_count": item.file_count}
            for item in skills.list()
        ]

    @api.get("/skills/{name}")
    async def skill_package(name: str) -> dict[str, Any]:
        package = skills.package(name)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @api.put("/skills/{name}")
    async def write_skill(name: str, body: SkillPackageRequest) -> dict[str, Any]:
        package = skills.write_package(name, body.files)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @api.post("/skills/import", status_code=201)
    async def import_skill_archive(
        file: Annotated[UploadFile, File()],
        name: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        content = await file.read(MAX_ARCHIVE_BYTES + 1)
        package = skills.import_archive(file.filename or "skill.zip", content, name)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @api.post("/skills/{name}", status_code=201)
    async def create_skill(name: str, body: SkillPackageRequest) -> dict[str, Any]:
        package = skills.create(name, body.files)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

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

    @app.post("/internal/blackboard/{task_id}/{agent_id}/checkpoint")
    async def blackboard_checkpoint(task_id: UUID, agent_id: str, body: dict) -> dict:
        worker = config.agents.agents[agent_id].role == 'worker'
        if worker and (body.get('completion_hint') or publications.final_pending(task_id, agent_id)):
            await coordinator.aoe.protect_completion(task_id)
        file_sync = worker and bool(body.get('file_sync'))
        receipts = await publications.sync_findings(task_id, agent_id) if file_sync else []
        pending_final = file_sync and publications.final_pending(task_id, agent_id)
        result = await delivery.checkpoint(
            task_id, agent_id, ack=str(body.get("ack", "")), final=bool(body.get("final", False)) or pending_final,
            ack_only=bool(body.get("ack_only", False)),
            initial_only=bool(body.get("initial_only", False))
        )
        if file_sync:
            receipts.extend(await publications.sync_final(task_id, agent_id))
        if receipts:
            result = {**result, 'publication_receipts': receipts}
        if worker and not body.get("ack_only"):
            advice = await asyncio.to_thread(
                automation.experience.deliver, task_id, agent_id,
                str(body.get("experience_generation", "")), str(body.get("experience_ack", "")),
                body.get("experience_initial"),
            )
            if advice:
                result = {**result, "experience_context": advice["context"],
                          "experience_fingerprints": advice["fingerprints"],
                          "experience_delivery_id": advice["delivery_id"]}
        denied = (result.get('context') and (body.get('final') or body.get('force_start'))
                  or body.get('defer_context') and any(result.get(k) for k in
                      ['context', 'experience_context']))
        if worker and body.get('action_start') and not denied:
            await coordinator.aoe.action_started(task_id, agent_id, str(body['action_start']))
        return result

    @app.websocket("/internal/agents/{task_id}/{agent_id}")
    async def agent_socket(websocket: WebSocket, task_id: UUID, agent_id: str) -> None:
        await coordinator.gateway.attach(task_id, agent_id, websocket)

    @api.get("/knowledge-sources")
    async def knowledge_sources():
        return {"online": {"available": bool(config.runtime.ctf_knowledge_mcp_url.strip())},
                "local": await asyncio.to_thread(local_knowledge.status)}

    app.include_router(api)
    app.mount("/local-knowledge-mcp", local_knowledge_mcp.streamable_http_app())
    app.mount("/scout-mcp", scout_mcp.streamable_http_app())
    app.mount("/aoe-scout-mcp", aoe_scout_mcp.streamable_http_app())
    app.mount('/web-mcp', web_mcp.streamable_http_app())
    return app


__all__ = ["create_app"]
