"""Persisted model, prompt and Skill settings that directly configure Runtime."""

# FastAPI dependencies are intentionally declared in function defaults.
# ruff: noqa: B008

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import SecretStr

from apps.api.dependencies import container
from tga2.bootstrap import Container
from tga2.integrations.model import ModelSettings, build_chat_model
from tga2.skills import Skill

router = APIRouter(tags=["settings"])


def _llm(settings: ModelSettings):
    return {
        "configured": settings.can_call_model,
        "base_url": settings.base_url or "",
        "model": settings.model,
        "api_key_set": settings.api_key is not None,
        "browser_configured": settings.api_key is not None,
        "supports_vision": None,
        "max_output_tokens": 8192,
        "timeout_seconds": 120,
        "temperature": settings.temperature or 0,
        "reasoning_mode": "auto",
        "verification_status": "verified"
        if settings.verified
        else "unverified"
        if settings.can_call_model
        else "failed",
        "verification": {
            "status": "verified"
            if settings.verified
            else "unverified"
            if settings.can_call_model
            else "failed",
            "verified_at": None,
            "last_error": None,
        },
    }


@router.get("/settings/llm")
def llm(app: Container = Depends(container)):
    return _llm(app.configuration.model)


@router.post("/settings/llm")
def update_llm(payload: dict, app: Container = Depends(container)):
    current = app.configuration.model
    settings = ModelSettings(
        provider=str(payload.get("provider") or current.provider),
        model=str(payload.get("model") or current.model),
        api_key=payload.get("api_key") or current.api_key,
        base_url=payload.get("base_url") or current.base_url,
        temperature=payload.get("temperature", current.temperature),
        offline=not bool(payload.get("api_key") or current.api_key),
        verified=False,
    )
    app.runtime.configure_model(settings)
    return _llm(settings)


@router.post("/settings/llm/verify")
def verify_llm(app: Container = Depends(container)):
    settings = app.configuration.model
    if not settings.can_call_model:
        raise HTTPException(409, "model is not configured")
    try:
        response = build_chat_model(settings).invoke("Reply with exactly OK.")
    except Exception as exc:
        raise HTTPException(
            502, {"code": "MODEL_VERIFICATION_FAILED", "message": str(exc)}
        ) from exc
    settings = settings.model_copy(update={"verified": True})
    app.runtime.configure_model(settings)
    return {
        "configured": True,
        "reachable": True,
        "action_tools": True,
        "model": settings.model,
        "verification_status": "verified",
        "capabilities": {"tool_calling": None, "structured_output": True},
        "tool_catalog": {
            "tool_count": 5 + len(app.runtime.external_tools),
            "schema_bytes": 0,
            "accepted": True,
        },
        "response_id": getattr(response, "id", None),
    }


@router.get("/settings/llm/providers")
def providers(app: Container = Depends(container)):
    settings = app.configuration.model
    values = [
        {
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
        }
    ]
    if settings.api_key:
        values.append(_provider(settings))
    return {
        "schema_version": 1,
        "presets": [
            {"id": "offline", "name": "Offline demo", "base_url": ""},
            {
                "id": "openai",
                "name": "OpenAI-compatible",
                "base_url": "https://api.openai.com/v1",
            },
        ],
        "providers": values,
    }


@router.post("/settings/llm/providers", status_code=201)
def create_provider(payload: dict, app: Container = Depends(container)):
    settings = ModelSettings(
        provider=str(payload.get("preset_id") or "openai"),
        model=str(payload.get("model") or "gpt-5-mini"),
        api_key=SecretStr(str(payload.get("api_key") or "")),
        base_url=str(payload.get("base_url") or "") or None,
        verified=False,
    )
    if not settings.api_key.get_secret_value():
        raise HTTPException(422, "api_key is required")
    app.runtime.configure_model(settings)
    return {
        "provider": _provider(settings, str(payload.get("name") or settings.provider))
    }


@router.post("/settings/llm/providers/{provider_id}/models", status_code=201)
def add_model(provider_id: str, payload: dict, app: Container = Depends(container)):
    settings = app.configuration.model.model_copy(
        update={"model": str(payload.get("name") or "default"), "verified": False}
    )
    app.runtime.configure_model(settings)
    return {"model": _model(settings.model)}


@router.post("/settings/llm/providers/{provider_id}/api-keys", status_code=201)
def add_key(provider_id: str, payload: dict, app: Container = Depends(container)):
    secret = str(payload.get("api_key") or "")
    if not secret:
        raise HTTPException(422, "api_key is required")
    settings = app.configuration.model.model_copy(
        update={"api_key": SecretStr(secret), "offline": False, "verified": False}
    )
    app.runtime.configure_model(settings)
    return {
        "api_key": {
            "id": "active",
            "label": str(payload.get("label") or "Active"),
            "masked": f"****{secret[-4:]}",
            "selected": True,
        }
    }


@router.put("/settings/llm/providers/{provider_id}/api-keys/{key_id}/selection")
def select_key(provider_id: str, key_id: str, app: Container = Depends(container)):
    return {"provider": _provider(app.configuration.model)}


@router.post("/settings/llm/providers/{provider_id}/models/{model_id}/verify")
def verify_model(provider_id: str, model_id: str, app: Container = Depends(container)):
    return verify_llm(app)


@router.get("/settings/llm/agent-options")
def agent_options(mode: str = "ctf", app: Container = Depends(container)):
    settings = app.configuration.model
    catalog = providers(app)
    active_provider = (
        settings.provider if settings.can_call_model and settings.verified else "offline"
    )
    models = [
        {
            "provider_id": p["id"],
            "provider_name": p["name"],
            "model_id": m["id"],
            "model_name": m["name"],
            "api_key_id": p["selected_api_key_id"],
            "verification_status": m["verification_status"],
            "ready": p["id"] == "offline" or m["verification_status"] == "verified",
        }
        for p in catalog["providers"]
        if p["id"] == active_provider
        for m in p["models"]
    ]
    return {
        "mode": mode,
        "agents": [
            {"id": role, "role": role, "specialties": ["evidence"], "required": True}
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
        "schema_version": 1,
        "skills": [_skill(item) for item in app.runtime.skills.list()],
    }


@router.get("/settings/skills/{name}")
def skill(name: str, app: Container = Depends(container)):
    item = app.runtime.skills.get(name)
    if item is None:
        raise HTTPException(404, "skill not found")
    return {"skill": {**_skill(item), "body": item.content}}


@router.post("/settings/skills/import", status_code=201)
async def import_skill(request: Request, app: Container = Depends(container)):
    filename = unquote(request.headers.get("x-tga-filename") or "custom-skill.md")
    body = (await request.body()).decode("utf-8")
    if len(body.encode()) > 512_000:
        raise HTTPException(413, "skill exceeds 512 KB")
    name = _identifier(Path(filename).stem)
    scene = request.headers.get("x-tga-scene")
    item = Skill(
        name=name,
        description=f"Imported skill for {scene or 'all modes'}",
        tags=(scene,) if scene else (),
        content=body,
    )
    try:
        app.runtime.skills.save(item)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"skill": {**_skill(item), "body": item.content}}


@router.put("/settings/skills/{name}")
def update_skill(name: str, payload: dict, app: Container = Depends(container)):
    current = app.runtime.skills.get(name)
    if current is None:
        raise HTTPException(404, "skill not found")
    if current.source == "builtin":
        raise HTTPException(409, "builtin skills are read-only")
    item = Skill(
        name=name,
        description=str(payload.get("summary") or current.description),
        tags=tuple(payload.get("tags") or current.tags),
        content=str(payload.get("body") or current.content),
        enabled=True,
    )
    app.runtime.skills.save(item)
    return {"skill": {**_skill(item), "body": item.content}}


@router.delete("/settings/skills/{name}")
def delete_skill(name: str, app: Container = Depends(container)):
    item = app.runtime.skills.get(name)
    if item is not None and item.source == "builtin":
        raise HTTPException(409, "builtin skills are read-only")
    return {"name": name, "deleted": app.runtime.skills.delete(name)}


def _identifier(value: str) -> str:
    return (
        "-".join(
            "".join(ch if ch.isalnum() else "-" for ch in value.casefold()).split()
        )[:64]
        or uuid4().hex[:8]
    )


def _model(name: str):
    return {
        "id": _identifier(name),
        "name": name,
        "max_output_tokens": 8192,
        "timeout_seconds": 120,
        "temperature": 0,
        "reasoning_mode": "auto",
        "verification_status": "unverified",
        "verification": {
            "status": "unverified",
            "verified_at": None,
            "last_error": None,
        },
    }


def _active_model(settings: ModelSettings):
    value = _model(settings.model)
    if settings.verified:
        value["verification_status"] = "verified"
        value["verification"]["status"] = "verified"
    return value


def _provider(settings: ModelSettings, name: str | None = None):
    secret = settings.api_key.get_secret_value() if settings.api_key else ""
    return {
        "id": settings.provider,
        "name": name or settings.provider.title(),
        "preset_id": settings.provider,
        "base_url": settings.base_url or "",
        "models": [_active_model(settings)],
        "api_keys": [
            {
                "id": "active",
                "label": "Active",
                "masked": f"****{secret[-4:]}" if secret else "",
                "selected": True,
            }
        ],
        "selected_api_key_id": "active",
    }


def _skill(item: Skill):
    return {
        "name": item.name,
        "modes": [],
        "capabilities": [],
        "tags": list(item.tags),
        "version": "1",
        "source": item.source,
        "summary": item.description,
        "editable": item.source == "custom",
    }
