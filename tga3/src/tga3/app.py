"""TGA3 control-plane FastAPI application assembly."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .blackboard_delivery import BlackboardDelivery
from .config import TGA3Config
from .coordinator import TaskCoordinator
from .docker_runtime import ContainerRuntime
from .errors import TGA3Error
from .file_publications import FilePublications
from .host_agents import Automation, CliHostModel, HostModel
from .knowledge_relay import KnowledgeRelay
from .local_knowledge import LocalKnowledge, build_local_knowledge_mcp
from .mcp_server import build_scout_mcp, build_web_mcp
from .model_discovery import discover_provider_models
from .routes import (
    AppServices,
    ExtendQuestionRequest,
    ModelEnabledRequest,
    ModelRequest,
    RealtimePromptRequest,
    RestartTaskRequest,
    ScoutSettingsRequest,
    SkillPackageRequest,
    build_aoe_router,
    build_config_router,
    build_internal_router,
    build_skills_router,
    build_tasks_router,
)
from .skills import SkillCatalog
from .storage import Storage
from .submission_guard import SubmissionGuard
from .token_usage import TokenUsageTracker
from .web_search import WebSearchService


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
        should_auto_report=lambda task: coordinator.orchestrator_for(task).auto_report_on_blackboard_change,
    )
    coordinator.attach_automation(automation)
    coordinator.change_handlers.append(automation.changed)

    scout_mcp = build_scout_mcp(config, coordinator.scout_intel)
    aoe_scout_mcp = build_scout_mcp(config, coordinator.scout_intel, aoe=coordinator.aoe)
    web_search = WebSearchService(config, storage)
    knowledge_relay = KnowledgeRelay(config)
    local_knowledge = LocalKnowledge(config)
    local_knowledge_mcp = build_local_knowledge_mcp(config, local_knowledge)
    web_mcp = build_web_mcp(config, web_search)
    delivery = BlackboardDelivery(config, storage, coordinator.blackboard)
    usage = TokenUsageTracker(config, storage)
    publications = FilePublications(config, coordinator.blackboard, submissions=coordinator.aoe.submissions)
    submission_guard = SubmissionGuard(config, storage)

    services = AppServices(
        config=config,
        storage=storage,
        containers=containers,
        coordinator=coordinator,
        automation=automation,
        skills=skills,
        delivery=delivery,
        usage=usage,
        publications=publications,
        submission_guard=submission_guard,
        web_search=web_search,
        knowledge_relay=knowledge_relay,
        local_knowledge=local_knowledge,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with (
            scout_mcp.session_manager.run(),
            aoe_scout_mcp.session_manager.run(),
            web_mcp.session_manager.run(),
            local_knowledge_mcp.session_manager.run(),
        ):
            await coordinator.restore_tasks()

            async def clean_target_leases() -> None:
                while True:
                    parents = {v["parent_id"] for v in coordinator.aoe.targets.records.values() if v.get("owned")}
                    for parent in parents:
                        try:
                            await coordinator.aoe.targets.reconcile(
                                await storage.list_questions(UUID(parent)),
                                parent_id=UUID(parent),
                            )
                        except Exception as exc:
                            # A transient database/network failure must not permanently stop cleanup.
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
    app.state.services = services
    app.state.config = config
    app.state.storage = storage
    app.state.coordinator = coordinator
    app.state.automation = automation
    app.state.publications = publications
    app.state.submission_guard = submission_guard
    app.state.web_search = web_search
    app.state.knowledge_relay = knowledge_relay
    app.state.experience = automation.experience

    @app.exception_handler(TGA3Error)
    async def tga3_error(_request: Request, exc: TGA3Error) -> JSONResponse:
        status = 404 if exc.code == "not_found" else 409 if exc.code == "conflict" else 422
        return JSONResponse(status_code=status, content={"error": exc.code, "message": str(exc)})

    @app.exception_handler(KeyError)
    @app.exception_handler(ValueError)
    async def configuration_error(_request: Request, exc: KeyError | ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_configuration", "message": str(exc)})

    api = APIRouter(prefix="/api/v3")
    api.include_router(build_tasks_router(services))
    api.include_router(build_aoe_router(services))
    api.include_router(build_config_router(services))
    api.include_router(build_skills_router(services))

    app.include_router(api)
    app.include_router(build_internal_router(services))
    app.mount("/local-knowledge-mcp", local_knowledge_mcp.streamable_http_app())
    app.mount("/scout-mcp", scout_mcp.streamable_http_app())
    app.mount("/aoe-scout-mcp", aoe_scout_mcp.streamable_http_app())
    app.mount("/web-mcp", web_mcp.streamable_http_app())
    return app


__all__ = [
    "ExtendQuestionRequest",
    "ModelEnabledRequest",
    "ModelRequest",
    "RealtimePromptRequest",
    "RestartTaskRequest",
    "ScoutSettingsRequest",
    "SkillPackageRequest",
    "create_app",
    "discover_provider_models",
]
