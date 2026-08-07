"""Llm Settings HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from urllib.parse import urlsplit

from tga.models.bootstrap import build_model_client, model_config_status
from tga.models.capability_probe import ProviderCapabilityProbe
from tga.models.settings import (
    record_model_verification,
    record_model_verification_failure,
    record_model_verification_started,
    save_model_settings,
)
from tga.models.provider_catalog import (
    add_provider_api_key,
    add_provider_model,
    build_catalog_model_client,
    create_provider,
    list_provider_catalog,
    model_target_status,
    record_catalog_verification,
    record_catalog_verification_failure,
    select_provider_api_key,
)
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry
from tga.modes import normalize_mode

from apps.api.routes.support import LLMSettingsRequest

router = APIRouter(tags=["llm-settings"])


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    preset_id: str | None = Field(default=None, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=255)
    api_key: SecretStr = Field(max_length=16_384)
    api_key_label: str | None = Field(default=None, max_length=128)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        clean = value.strip().rstrip("/")
        parsed = urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query, or fragment")
        return clean


class ProviderModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    supports_vision: bool | None = None
    max_output_tokens: int = Field(default=1024, ge=256, le=16_384)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    temperature: float = Field(default=0.2, ge=0, le=2)
    reasoning_mode: str = Field(default="auto", pattern=r"^(auto|enabled|disabled)$")


class ProviderAPIKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(max_length=16_384)
    label: str | None = Field(default=None, max_length=128)


@router.get("/settings/llm")
def llm_settings() -> dict[str, Any]:
    status = model_config_status()
    return {
        "configured": bool(status["configured"]),
        "base_url": str(status.get("base_url") or ""),
        "model": str(status.get("model") or ""),
        "api_key_set": bool(status.get("api_key_set")),
        "browser_configured": bool(status.get("browser_configured")),
        "supports_vision": status.get("supports_vision"),
        "max_output_tokens": status.get("max_output_tokens"),
        "timeout_seconds": status.get("timeout_seconds"),
        "temperature": status.get("temperature"),
        "reasoning_mode": status.get("reasoning_mode"),
        "verification_status": status.get("verification_status"),
        "verification": status.get("verification"),
    }


@router.post("/settings/llm")
def update_llm_settings(payload: LLMSettingsRequest) -> dict[str, Any]:
    key = payload.api_key.get_secret_value() if payload.api_key is not None else None
    save_model_settings(
        base_url=payload.base_url,
        model=payload.model,
        api_key=key,
        supports_vision=payload.supports_vision,
        max_output_tokens=payload.max_output_tokens,
        timeout_seconds=payload.timeout_seconds,
        temperature=payload.temperature,
        reasoning_mode=payload.reasoning_mode,
    )
    return llm_settings()


@router.post("/settings/llm/verify")
def verify_llm_settings() -> dict[str, Any]:
    """Make an explicit, low-cost action-tool check before a task starts.

    Configuration presence alone cannot detect an invalid model identifier,
    expired key, or an incompatible Function Calling dialect.  This endpoint
    is never called automatically and never returns the key or response body.
    """
    client = build_model_client()
    if client is None:
        raise HTTPException(status_code=409, detail="model is not configured")
    record_model_verification_started()
    try:
        result = ProviderCapabilityProbe(client).verify()
    except Exception as exc:
        record_model_verification_failure(code="TOOL_PROTOCOL_VERIFICATION_FAILED", message=str(exc))
        raise HTTPException(
            status_code=502,
            detail={"code": "MODEL_VERIFICATION_FAILED", "message": "model tool protocol verification failed"},
        ) from exc
    capabilities = result["capabilities"]
    verification = record_model_verification(
        tool_calling=True,
        vision=capabilities["vision"],
        capabilities=capabilities,
    )
    return {
        "configured": True,
        "reachable": True,
        "action_tools": True,
        "model": getattr(client, "model", ""),
        "request_id": result.get("request_id"),
        "verification_status": verification["status"],
        "capabilities": verification["capabilities"],
        "tool_catalog": result["tool_catalog"],
    }


@router.get("/settings/llm/providers")
def providers() -> dict[str, Any]:
    return list_provider_catalog()


@router.post("/settings/llm/providers", status_code=201)
def add_provider(payload: ProviderCreateRequest) -> dict[str, Any]:
    try:
        provider = create_provider(
            name=payload.name, base_url=payload.base_url, preset_id=payload.preset_id,
            model=payload.model, api_key=payload.api_key.get_secret_value(),
            api_key_label=payload.api_key_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"provider": provider}


@router.post("/settings/llm/providers/{provider_id}/models", status_code=201)
def add_model(provider_id: str, payload: ProviderModelRequest) -> dict[str, Any]:
    try:
        model = add_provider_model(
            provider_id=provider_id, name=payload.name,
            supports_vision=payload.supports_vision,
            max_output_tokens=payload.max_output_tokens,
            timeout_seconds=payload.timeout_seconds,
            temperature=payload.temperature,
            reasoning_mode=payload.reasoning_mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="provider not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"model": model}


@router.post("/settings/llm/providers/{provider_id}/api-keys", status_code=201)
def add_api_key(provider_id: str, payload: ProviderAPIKeyRequest) -> dict[str, Any]:
    try:
        key = add_provider_api_key(
            provider_id=provider_id, api_key=payload.api_key.get_secret_value(), label=payload.label,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="provider not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"api_key": key}


@router.put("/settings/llm/providers/{provider_id}/api-keys/{api_key_id}/selection")
def select_api_key(provider_id: str, api_key_id: str) -> dict[str, Any]:
    try:
        provider = select_provider_api_key(provider_id=provider_id, api_key_id=api_key_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="provider or API key not found") from exc
    return {"provider": provider}


@router.post("/settings/llm/providers/{provider_id}/models/{model_id}/verify")
def verify_provider_model(provider_id: str, model_id: str) -> dict[str, Any]:
    try:
        client = build_catalog_model_client(provider_id=provider_id, model_id=model_id)
        result = ProviderCapabilityProbe(client).verify()
        capabilities = result["capabilities"]
        verification = record_catalog_verification(
            provider_id=provider_id, model_id=model_id, capabilities=capabilities,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="provider, model, or selected API key not found") from exc
    except Exception as exc:
        try:
            record_catalog_verification_failure(
                provider_id=provider_id, model_id=model_id,
                code="TOOL_PROTOCOL_VERIFICATION_FAILED", message=str(exc),
            )
        except (KeyError, ValueError):
            pass
        raise HTTPException(
            status_code=502,
            detail={"code": "MODEL_VERIFICATION_FAILED", "message": "model tool protocol verification failed"},
        ) from exc
    return {
        "reachable": True, "action_tools": True, "model": getattr(client, "model", ""),
        "verification_status": verification["status"], "capabilities": verification["capabilities"],
        "tool_catalog": result["tool_catalog"],
    }


@router.get("/settings/llm/agent-options")
def agent_model_options(mode: str = Query(max_length=64)) -> dict[str, Any]:
    try:
        normalized = normalize_mode(mode)
        definitions = SolverDefinitionRegistry.builtin()
        template = TeamTemplateRegistry.builtin(definitions=definitions).require(normalized)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ordered_ids = list(dict.fromkeys((
        template.supervisor_definition_id,
        *template.available_solver_definition_ids,
        template.reviewer_definition_id,
        template.reporter_definition_id,
    )))
    agents = []
    for definition_id in ordered_ids:
        definition = definitions.require(definition_id)
        agents.append({
            "id": definition.id,
            "role": definition.orchestration_role,
            "specialties": list(definition.specialties),
            "required": definition.id in {
                template.supervisor_definition_id, template.reviewer_definition_id,
                template.reporter_definition_id, *template.required_solver_definition_ids,
            },
        })
    catalog = list_provider_catalog()
    options = []
    for provider in catalog["providers"]:
        for model in provider["models"]:
            try:
                status = model_target_status(provider_id=provider["id"], model_id=model["id"])
            except KeyError:
                continue
            options.append({
                "provider_id": provider["id"], "provider_name": provider["name"],
                "model_id": model["id"], "model_name": model["name"],
                "api_key_id": status["api_key_id"],
                "verification_status": status["verification_status"],
                "ready": status["configured"] and status["verification_status"] == "verified",
            })
    return {"mode": normalized, "agents": agents, "models": options}
