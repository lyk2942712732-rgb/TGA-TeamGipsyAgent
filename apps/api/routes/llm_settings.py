"""Llm Settings HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from tga.models.bootstrap import build_model_client, model_config_status
from tga.models.capability_probe import ProviderCapabilityProbe
from tga.models.settings import (
    record_model_verification,
    record_model_verification_failure,
    record_model_verification_started,
    save_model_settings,
)

from apps.api.routes.support import LLMSettingsRequest

router = APIRouter(tags=["llm-settings"])


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
