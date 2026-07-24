"""Editable Agent prompt settings used by newly initialized sessions."""

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from apps.api.routes.support import _api_error
from tga.runtime.prompt_settings import (
    AgentPromptSettings,
    load_agent_prompt_settings,
    save_agent_prompt_settings,
)


router = APIRouter(tags=["agent-prompts"])


@router.get("/settings/agent-prompts")
def get_agent_prompt_settings() -> dict:
    return load_agent_prompt_settings().model_dump(mode="json")


@router.put("/settings/agent-prompts")
def update_agent_prompt_settings(payload: AgentPromptSettings) -> dict:
    try:
        saved = save_agent_prompt_settings(payload)
    except (OSError, ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=_api_error("INVALID_AGENT_PROMPTS", str(exc)),
        ) from exc
    return saved.model_dump(mode="json")
