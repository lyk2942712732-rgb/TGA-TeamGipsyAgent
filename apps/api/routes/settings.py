"""Persisted model, prompt and Skill settings used by the real Runtime."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import SecretStr

from apps.api.dependencies import container
from tga2.agent.schemas import PlanDraft, structured_output_prompt
from tga2.bootstrap import Container
from tga2.integrations.model import (
    ModelRegistry,
    ModelSettings,
    RegisteredAPIKey,
    RegisteredModel,
    RegisteredProvider,
    build_chat_model,
    utc_now,
)
from tga2.skills import SkillDocument, SkillPackage

router = APIRouter(tags=["settings"])


class _StructuredOutputInvalidError(ValueError):
    """The endpoint was reachable but did not satisfy the supplied JSON schema."""


def _active(app: Container) -> tuple[RegisteredProvider, RegisteredModel] | None:
    registry = app.configuration.model_registry
    if not registry.active_provider_id or not registry.active_model_id:
        return None
    try:
        provider = registry.provider(registry.active_provider_id)
        return provider, provider.model(registry.active_model_id)
    except KeyError:
        return None


def _has_configured_model(registry: ModelRegistry) -> bool:
    return any(
        provider.models and provider.api_keys and provider.selected_api_key_id
        for provider in registry.providers
    )


def _llm(app: Container) -> dict:
    registry = app.configuration.model_registry
    active = _active(app)
    provider, model = active if active else (None, None)
    configured = _has_configured_model(registry)
    status = (
        model.verification_status if model else "unverified" if configured else "failed"
    )
    return {
        "configured": configured,
        "active": active is not None and status == "verified",
        "provider_id": provider.id if provider else None,
        "provider_name": provider.name if provider else None,
        "base_url": provider.base_url or "" if provider else "",
        "model_id": model.id if model else None,
        "model": model.name if model else "offline",
        "api_key_set": bool(provider and provider.selected_api_key_id),
        "browser_configured": configured,
        "supports_vision": None,
        "max_output_tokens": model.max_output_tokens if model else 8192,
        "timeout_seconds": model.timeout_seconds if model else 120,
        "temperature": model.temperature or 0 if model else 0,
        "reasoning_mode": model.reasoning_mode if model else "disabled",
        "verification_status": status,
        "verification": {
            "status": status,
            "verified_at": model.verified_at if model else None,
            "last_error": model.last_error if model else None,
        },
    }


@router.get("/settings/llm")
def llm(app: Container = Depends(container)):
    return _llm(app)


@router.post("/settings/llm")
def update_llm(payload: dict, app: Container = Depends(container)):
    """Compatibility write for older clients; it creates one real provider."""
    current = (
        app.configuration.model_registry.active_settings() or ModelSettings.from_env()
    )
    secret = payload.get("api_key")
    key = SecretStr(str(secret)) if secret else current.api_key
    if key is None or not key.get_secret_value():
        raise HTTPException(422, "api_key is required")
    provider = RegisteredProvider(
        name=str(
            payload.get("provider_name")
            or payload.get("provider")
            or "OpenAI-compatible"
        ),
        preset_id=str(payload.get("preset_id") or "custom"),
        model_provider="openai",
        base_url=str(payload.get("base_url") or current.base_url or "") or None,
        models=[RegisteredModel(name=str(payload.get("model") or current.model))],
    )
    api_key = RegisteredAPIKey(label="Active", api_key=key)
    provider.api_keys.append(api_key)
    provider.selected_api_key_id = api_key.id
    registry = app.configuration.model_registry
    registry.providers.append(provider)
    registry.active_provider_id = provider.id
    registry.active_model_id = provider.models[0].id
    app.configuration.save_model_registry()
    app.runtime.configure_model(provider.settings(provider.models[0].id))
    return _llm(app)


@router.post("/settings/llm/verify")
def verify_llm(app: Container = Depends(container)):
    active = _active(app)
    if active is None:
        raise HTTPException(409, "select a provider model before verification")
    return _verify(active[0].id, active[1].id, app)


@router.get("/settings/llm/providers")
def providers(app: Container = Depends(container)):
    return {
        "schema_version": 1,
        "presets": app.configuration.model_registry.presets,
        "providers": [
            _offline_provider(),
            *(
                _provider(item, app.configuration.model_registry)
                for item in app.configuration.model_registry.providers
            ),
        ],
    }


@router.post("/settings/llm/providers", status_code=201)
def create_provider(payload: dict, app: Container = Depends(container)):
    name = str(payload.get("name") or "").strip()
    model_name = str(payload.get("model") or "").strip()
    secret = str(payload.get("api_key") or "")
    if not name or not model_name or not secret:
        raise HTTPException(422, "name, model and api_key are required")
    provider = RegisteredProvider(
        name=name,
        preset_id=str(payload.get("preset_id") or "custom"),
        # This page currently promises OpenAI-compatible endpoints.  Keep the
        # transport adapter separate from the display/preset identity.
        model_provider="openai",
        base_url=str(payload.get("base_url") or "").strip() or None,
        models=[RegisteredModel(name=model_name)],
    )
    key = RegisteredAPIKey(
        label=str(payload.get("api_key_label") or "Active"),
        api_key=SecretStr(secret),
    )
    provider.api_keys.append(key)
    provider.selected_api_key_id = key.id
    registry = app.configuration.model_registry
    registry.providers.append(provider)
    registry.active_provider_id = provider.id
    registry.active_model_id = provider.models[0].id
    app.configuration.save_model_registry()
    app.runtime.configure_model(provider.settings(provider.models[0].id))
    return {"provider": _provider(provider, app.configuration.model_registry)}


@router.post("/settings/llm/providers/{provider_id}/models", status_code=201)
def add_model(provider_id: str, payload: dict, app: Container = Depends(container)):
    provider = _get_provider(provider_id, app)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "model name is required")
    if any(item.name == name for item in provider.models):
        raise HTTPException(409, "model already exists")
    model = RegisteredModel(name=name)
    provider.models.append(model)
    provider.updated_at = utc_now()
    app.configuration.save_model_registry()
    return {"model": _model(model)}


@router.post("/settings/llm/providers/{provider_id}/api-keys", status_code=201)
def add_key(provider_id: str, payload: dict, app: Container = Depends(container)):
    provider = _get_provider(provider_id, app)
    secret = str(payload.get("api_key") or "")
    if not secret:
        raise HTTPException(422, "api_key is required")
    key = RegisteredAPIKey(
        label=str(payload.get("label") or "Active"), api_key=SecretStr(secret)
    )
    provider.api_keys.append(key)
    provider.selected_api_key_id = key.id
    provider.updated_at = utc_now()
    for model in provider.models:
        model.verification_status = "stale" if model.verified_at else "unverified"
        model.last_error = None
    app.configuration.save_model_registry()
    _deactivate_if_active(provider, app)
    return {"api_key": _api_key(key, selected=True)}


@router.put("/settings/llm/providers/{provider_id}/api-keys/{key_id}/selection")
def select_key(provider_id: str, key_id: str, app: Container = Depends(container)):
    provider = _get_provider(provider_id, app)
    if not any(item.id == key_id for item in provider.api_keys):
        raise HTTPException(404, "API key not found")
    if provider.selected_api_key_id != key_id:
        provider.selected_api_key_id = key_id
        provider.updated_at = utc_now()
        for model in provider.models:
            model.verification_status = "stale" if model.verified_at else "unverified"
            model.last_error = None
        app.configuration.save_model_registry()
        _deactivate_if_active(provider, app)
    return {"provider": _provider(provider, app.configuration.model_registry)}


@router.post("/settings/llm/providers/{provider_id}/models/{model_id}/verify")
def verify_model(provider_id: str, model_id: str, app: Container = Depends(container)):
    return _verify(provider_id, model_id, app)


def _verify(provider_id: str, model_id: str, app: Container) -> dict:
    provider = _get_provider(provider_id, app)
    try:
        model = provider.model(model_id)
        settings = provider.settings(model_id)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc
    model.verification_status = "verifying"
    model.last_error = None
    app.configuration.save_model_registry()
    try:
        structured = build_chat_model(settings).with_structured_output(
            PlanDraft, method="json_mode", include_raw=True
        )
        messages = [
            {
                "role": "system",
                "content": structured_output_prompt(
                    "You are checking whether this model can produce reliable "
                    "structured output.",
                    (
                        "Create a minimal task plan containing exactly one item in "
                        "the `intents` array. Use the integer 50 for its priority."
                    ),
                    PlanDraft,
                ),
            },
            {
                "role": "user",
                "content": "Create a minimal JSON plan for inspecting one authorized input file.",
            },
        ]
        response = None
        parsing_error = None
        for attempt in range(2):
            response = structured.invoke(messages)
            parsed = response.get("parsed") if isinstance(response, dict) else response
            parsing_error = (
                response.get("parsing_error") if isinstance(response, dict) else None
            )
            if isinstance(parsed, PlanDraft):
                break
            if attempt == 0:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous JSON did not match the supplied schema. "
                            "Correct it: use the top-level `intents` array (not "
                            "`intent`) and an integer priority. Return JSON only."
                        ),
                    }
                )
        else:
            raise _StructuredOutputInvalidError(
                f"model did not produce a valid PlanDraft: {parsing_error or 'empty parsed response'}"
            )
    except _StructuredOutputInvalidError as exc:
        model.verification_status = "failed"
        model.last_error = {
            "code": "MODEL_STRUCTURED_OUTPUT_INVALID",
            "message": "模型已连接，但连续两次返回的 JSON 都不符合运行时结构。",
            "reason": str(exc)[:300],
        }
        app.configuration.save_model_registry()
        if app.configuration.model_registry.active_provider_id == provider.id:
            app.runtime.configure_model(settings.model_copy(update={"verified": False}))
        raise HTTPException(502, model.last_error) from exc
    except Exception as exc:
        model.verification_status = "failed"
        model.last_error = {
            "code": "MODEL_VERIFICATION_FAILED",
            "message": str(exc)[:600],
        }
        app.configuration.save_model_registry()
        if app.configuration.model_registry.active_provider_id == provider.id:
            app.runtime.configure_model(settings.model_copy(update={"verified": False}))
        raise HTTPException(502, model.last_error) from exc
    model.verification_status = "verified"
    model.verified_at = utc_now()
    model.last_error = None
    registry = app.configuration.model_registry
    registry.active_provider_id = provider.id
    registry.active_model_id = model.id
    app.configuration.save_model_registry()
    verified = provider.settings(model.id, require_verified=True)
    app.runtime.configure_model(verified)
    return {
        "configured": True,
        "reachable": True,
        "action_tools": None,
        "provider_id": provider.id,
        "provider_name": provider.name,
        "model_id": model.id,
        "model": model.name,
        "verification_status": "verified",
        "capabilities": {"tool_calling": None, "structured_output": True},
        "tool_catalog": {
            "tool_count": 5 + len(app.runtime.external_tools),
            "schema_bytes": 0,
            "accepted": True,
        },
        "response_id": getattr(
            response.get("raw") if isinstance(response, dict) else response, "id", None
        ),
    }


@router.get("/settings/llm/agent-options")
def agent_options(mode: str = "ctf", app: Container = Depends(container)):
    models = [
        {
            "provider_id": provider.id,
            "provider_name": provider.name,
            "model_id": model.id,
            "model_name": model.name,
            "api_key_id": provider.selected_api_key_id or "",
            "verification_status": model.verification_status,
            "ready": model.verification_status == "verified"
            and bool(provider.selected_api_key_id),
        }
        for provider in app.configuration.model_registry.providers
        for model in provider.models
    ]
    models.append(
        {
            "provider_id": "offline",
            "provider_name": "Offline demo",
            "model_id": "offline",
            "model_name": "Rule-based demo",
            "api_key_id": "not-required",
            "verification_status": "verified",
            "ready": True,
        }
    )
    return {
        "mode": mode,
        "agents": [
            {
                "id": role,
                "role": role,
                "specialties": ["evidence"],
                "required": True,
                "model": app.configuration.role_model_status(role),
            }
            for role in ("supervisor", "worker", "reviewer", "reporter")
        ],
        "models": models,
    }


@router.get("/settings/agent-prompts")
def prompts(app: Container = Depends(container)):
    return app.configuration.prompt_payload()


@router.put("/settings/agent-prompts")
def update_prompts(payload: dict, app: Container = Depends(container)):
    value = app.configuration.update_prompts(payload)
    app.runtime.reload_configuration()
    return value


@router.get("/settings/skills")
def skills(app: Container = Depends(container)):
    return {
        "schema_version": 2,
        "root": str(app.runtime.skills.root),
        "skills": [_skill(item) for item in app.runtime.skills.list()],
    }


@router.post("/settings/skills", status_code=201)
def create_skill(payload: dict, app: Container = Depends(container)):
    try:
        item = app.runtime.skills.create(
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            tags=list(payload.get("tags") or []),
            version=str(payload.get("version") or "1"),
            instructions=str(
                payload.get("instructions")
                or "# Instructions\n\nDescribe when and how Worker should use this Skill."
            ),
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"skill": _skill_detail(item)}


@router.post("/settings/skills/import", status_code=201)
async def import_skill(request: Request, app: Container = Depends(container)):
    try:
        item = app.runtime.skills.install_zip(await request.body())
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        status = 413 if "limit" in str(exc) or "too large" in str(exc) else 422
        raise HTTPException(status, str(exc)) from exc
    return {"skill": _skill_detail(item)}


@router.get("/settings/skills/{name}")
def skill(name: str, app: Container = Depends(container)):
    item = app.runtime.skills.get(name)
    if item is None:
        raise HTTPException(404, "skill package not found")
    return {"skill": _skill_detail(item)}


@router.put("/settings/skills/{name}")
def update_skill(name: str, payload: dict, app: Container = Depends(container)):
    current = app.runtime.skills.get(name)
    if current is None:
        raise HTTPException(404, "skill package not found")
    try:
        item = app.runtime.skills.update(
            name,
            description=str(payload.get("description", current.description)),
            tags=list(payload.get("tags", current.tags)),
            version=str(payload.get("version", current.version)),
            instructions=str(payload.get("instructions", current.instructions)),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"skill": _skill_detail(item)}


@router.put("/settings/skills/{name}/documents")
def put_skill_document(
    name: str, payload: dict, app: Container = Depends(container)
):
    try:
        item = app.runtime.skills.add_document(
            name,
            str(payload.get("path") or ""),
            str(payload.get("content") or ""),
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        status = 413 if "limit" in str(exc) else 422
        raise HTTPException(status, str(exc)) from exc
    return {"skill": _skill_detail(item)}


@router.get("/settings/skills/{name}/documents/{path:path}")
def skill_document(name: str, path: str, app: Container = Depends(container)):
    try:
        package = app.runtime.skills.get_required(name)
        content = app.runtime.skills.read_document(name, path)
        normalized = path.replace("\\", "/").strip("/")
        document = next(item for item in package.documents if item.path == normalized)
    except (FileNotFoundError, StopIteration) as exc:
        raise HTTPException(404, "skill document not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"document": {**_skill_document(document), "content": content}}


@router.delete("/settings/skills/{name}/documents/{path:path}")
def delete_skill_document(
    name: str, path: str, app: Container = Depends(container)
):
    try:
        deleted = app.runtime.skills.delete_document(name, path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"name": name, "path": path, "deleted": deleted}


@router.delete("/settings/skills/{name}")
def delete_skill(name: str, app: Container = Depends(container)):
    return {"name": name, "deleted": app.runtime.skills.delete(name)}


def _get_provider(provider_id: str, app: Container) -> RegisteredProvider:
    if provider_id == "offline":
        raise HTTPException(409, "offline demo is read-only")
    try:
        return app.configuration.model_registry.provider(provider_id)
    except KeyError as exc:
        raise HTTPException(404, "provider not found") from exc


def _deactivate_if_active(provider: RegisteredProvider, app: Container) -> None:
    registry = app.configuration.model_registry
    if registry.active_provider_id != provider.id or not registry.active_model_id:
        return
    try:
        app.runtime.configure_model(provider.settings(registry.active_model_id))
    except KeyError:
        app.runtime.configure_model(ModelSettings())


def _model(model: RegisteredModel):
    return {
        "id": model.id,
        "name": model.name,
        "max_output_tokens": model.max_output_tokens,
        "timeout_seconds": model.timeout_seconds,
        "temperature": model.temperature or 0,
        "reasoning_mode": model.reasoning_mode,
        "verification_status": model.verification_status,
        "verification": {
            "status": model.verification_status,
            "verified_at": model.verified_at,
            "last_error": model.last_error,
        },
    }


def _api_key(key: RegisteredAPIKey, *, selected: bool):
    secret = key.api_key.get_secret_value()
    return {
        "id": key.id,
        "label": key.label,
        "masked": f"••••••••{secret[-4:]}",
        "selected": selected,
        "created_at": key.created_at,
    }


def _provider(provider: RegisteredProvider, registry: ModelRegistry):
    return {
        "id": provider.id,
        "name": provider.name,
        "preset_id": provider.preset_id,
        "base_url": provider.base_url or "",
        "models": [_model(item) for item in provider.models],
        "api_keys": [
            _api_key(item, selected=item.id == provider.selected_api_key_id)
            for item in provider.api_keys
        ],
        "selected_api_key_id": provider.selected_api_key_id,
        "active_model_id": registry.active_model_id
        if registry.active_provider_id == provider.id
        else None,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def _offline_provider():
    return {
        "id": "offline",
        "name": "Offline demo",
        "preset_id": "offline",
        "base_url": "",
        "models": [
            {
                "id": "offline",
                "name": "Rule-based demo",
                "max_output_tokens": 8192,
                "timeout_seconds": 120,
                "temperature": 0,
                "reasoning_mode": "disabled",
                "verification_status": "verified",
                "verification": {
                    "status": "verified",
                    "verified_at": None,
                    "last_error": None,
                },
            }
        ],
        "api_keys": [
            {
                "id": "not-required",
                "label": "No key required",
                "masked": "offline",
                "selected": True,
            }
        ],
        "selected_api_key_id": "not-required",
        "active_model_id": "offline",
    }


def _skill(item: SkillPackage):
    return {
        "name": item.name,
        "tags": list(item.tags),
        "version": item.version,
        "summary": item.description,
        "entrypoint": "SKILL.md",
        "file_count": len(item.documents),
        "total_bytes": item.total_bytes,
        "content_sha256": item.content_sha256,
        "enabled": item.enabled,
    }


def _skill_detail(item: SkillPackage):
    return {
        **_skill(item),
        "instructions": item.instructions,
        "documents": [_skill_document(document) for document in item.documents],
    }


def _skill_document(item: SkillDocument):
    return {
        "path": item.path,
        "title": item.title,
        "size": item.size,
        "sha256": item.sha256,
    }
