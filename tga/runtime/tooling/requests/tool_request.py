"""Provider call plus host-owned context entering tool governance."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga.runtime.tooling.requests.action_context import ActionContext
from tga.runtime.tooling.requests.model_intent import ModelToolIntent


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    model_intent: ModelToolIntent = Field(default_factory=ModelToolIntent)
    action_context: ActionContext
    tool_call_id: str = Field(min_length=1, max_length=256)


__all__ = ["ToolRequest"]
