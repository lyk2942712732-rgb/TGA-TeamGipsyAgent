"""Models, providers, agents, scenes, runtime configuration, and knowledge source routes."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from fastapi import APIRouter

from ..config import (
    RUNTIME_DISPLAY_NAMES,
    RUNTIME_SUPPORTED_PROTOCOLS,
    AgentsConfig,
    ModelConfig,
    ModelsConfig,
    RuntimeConfig,
    ScenesConfig,
)
from ..model_discovery import discover_provider_models as _default_discover_provider_models
from ..provider_profiles import PROVIDER_PROFILES, ProviderProtocol
from .services import AppServices, ModelEnabledRequest, ScoutSettingsRequest

_RUNTIME_NATIVE_TOOLS: dict[str, list[str]] = {
    "codex_cli": ["Shell", "Read", "Edit", "web_search"],
    "claude_cli": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "web_search", "WebSearch", "WebFetch"],
    "opencode_cli": ["bash", "read", "write", "edit", "web_search"],
    "qwen_cli": ["run_shell_command", "read_file", "write_file", "edit", "tool_search", "tool_call", "web_search"],
}

_WORKER_SHARED_TOOLS: list[str] = [
    "知识库 MCP",
    "Skills",
    "经验卡",
    "原生黑板同步 Hook",
    "/findings/ 独立 JSON",
    "系统导入最终候选文件",
    "按 action 自动关联复现方法和证据",
]

_SCOUT_SHARED_TOOLS: list[str] = [
    "第一轮：Shell / 安全工具 / logs 留存",
    "第二轮：只读 logs",
    "scout_write_intel → /intels/raw/scout-intels.json",
    "第三轮：Intel 合并",
    "scout_merge_intel → /intels/merged/scout-intels.json",
    "系统仅导入合并 Intel",
]

_REPORTER_SHARED_TOOLS: list[str] = [
    "Read snapshot",
    "Read evidence",
    "Read skills",
    "Markdown Writeup",
]


def _tools_for(role: str, runtime: str) -> list[str]:
    native = _RUNTIME_NATIVE_TOOLS.get(runtime, [])
    if role == "scout":
        return [*native, *_SCOUT_SHARED_TOOLS]
    if role == "reporter":
        return [*_REPORTER_SHARED_TOOLS]
    return [*native, *_WORKER_SHARED_TOOLS]


def build_config_router(services: AppServices) -> APIRouter:
    router = APIRouter()
    config = services.config
    containers = services.containers
    skills = services.skills
    local_knowledge = services.local_knowledge
    config_lock = services.config_lock

    @router.get("/models")
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

    @router.get("/agent-definitions")
    async def agent_definitions() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for agent_id, binding in config.agents.agents.items():
            try:
                image = config.image_for_runtime(binding.runtime, agent_id)
            except ValueError:
                image = None
            image_health = await containers.image_status(image) if image else None
            result.append(
                {
                    "id": agent_id,
                    "scout_timeout_seconds": config.runtime.scout_timeout_seconds if binding.role == "scout" else None,
                    "scout_extract_timeout_seconds": None,
                    **binding.model_dump(mode="json"),
                    "tools": _tools_for(binding.role, binding.runtime),
                    "image": image,
                    "image_health": image_health,
                }
            )
        return result

    @router.get("/runtimes")
    async def runtimes() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for runtime_id, display_name in RUNTIME_DISPLAY_NAMES.items():
            try:
                image = config.image_for_runtime(runtime_id)
            except ValueError:
                image = None
            image_health = await containers.image_status(image) if image else None
            items.append(
                {
                    "id": runtime_id,
                    "display_name": display_name,
                    "supported_protocols": list(RUNTIME_SUPPORTED_PROTOCOLS[runtime_id]),
                    "image": image,
                    "image_health": image_health,
                    "native_tools": list(_RUNTIME_NATIVE_TOOLS.get(runtime_id, [])),
                    "tools_by_role": {
                        role: _tools_for(role, runtime_id) for role in ("scout", "worker", "reporter")
                    },
                }
            )
        return items

    @router.get("/config/models")
    async def read_models_config() -> dict:
        return config.export_document("models")

    @router.put("/config/scout-settings")
    async def write_scout_settings(body: ScoutSettingsRequest) -> dict:
        async with config_lock:
            updated = config.runtime.model_copy(update={"scout_timeout_seconds": body.timeout_seconds})
            config.apply_document("runtime", updated)
        return {"scout_timeout_seconds": config.runtime.scout_timeout_seconds}

    @router.get("/config/model-provider-presets")
    async def read_model_provider_presets() -> list[dict[str, object]]:
        return [profile.public_dict() for profile in PROVIDER_PROFILES]

    @router.put("/config/models")
    async def write_models_config(body: ModelsConfig) -> dict:
        async with config_lock:
            config.apply_document("models", body)
        return config.export_document("models")

    @router.delete("/config/models/{provider_id}", status_code=204)
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

    @router.post("/config/models/{provider_id}/discover")
    async def discover_models(provider_id: str, protocol: ProviderProtocol) -> dict[str, Any]:
        snapshot = config.models.provider(provider_id).model_copy(deep=True)
        app_mod = sys.modules.get("tga3.app")
        discover_fn = getattr(app_mod, "discover_provider_models", _default_discover_provider_models)
        discovered = await discover_fn(snapshot, protocol)
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
                for model in endpoint.models
                if model.id not in discovered_ids
            ]
            config.apply_document("models", updated)
        return {
            "provider_id": provider_id,
            "protocol": protocol,
            "count": len(discovered),
            "models": [model.model_dump(mode="json") for model in endpoint.models],
        }

    @router.patch("/config/models/{provider_id}/endpoints/{protocol}/models")
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

    @router.get("/config/agents")
    async def read_agents_config() -> dict:
        return config.export_document("agents")

    @router.put("/config/agents")
    async def write_agents_config(body: AgentsConfig) -> dict:
        async with config_lock:
            config.apply_document("agents", body)
        return config.export_document("agents")

    @router.get("/config/scenes")
    async def read_scenes_config() -> dict:
        return config.export_document("scenes")

    @router.put("/config/scenes")
    async def write_scenes_config(body: ScenesConfig) -> dict:
        async with config_lock:
            config.apply_document("scenes", body)
        return config.export_document("scenes")

    @router.get("/config/runtime")
    async def read_runtime_config() -> dict:
        return config.export_document("runtime")

    @router.put("/config/runtime")
    async def write_runtime_config(body: RuntimeConfig) -> dict:
        async with config_lock:
            config.apply_document("runtime", body)
            skills.set_root(config.skills_root)
        return config.export_document("runtime")

    @router.get("/scenes")
    async def scenes() -> list[dict[str, str]]:
        return [
            {"id": scene.id, "name": scene.name, "description": scene.description} for scene in config.scenes.scenes
        ]

    @router.get("/knowledge-sources")
    async def knowledge_sources() -> dict[str, Any]:
        return {
            "online": {"available": bool(config.runtime.ctf_knowledge_mcp_url.strip())},
            "local": await asyncio.to_thread(local_knowledge.status),
        }

    return router
