"""Current durable event envelope."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: int = 6
    id: str
    task_id: str
    solver_id: str | None = None
    intent_id: str | None = None
    seq: int = Field(ge=1)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


__all__ = ["AgentEvent"]
