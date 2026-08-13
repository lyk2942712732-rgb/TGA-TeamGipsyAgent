"""Authoritative product policy; the model may never widen it."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from tga2.core.models import utc_now


class RiskLevel(StrEnum):
    PASSIVE = "passive"
    ACTIVE = "active"
    DESTRUCTIVE = "destructive"


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allowed_tools: frozenset[str] = frozenset()
    approval_required: frozenset[str] = frozenset()
    denied_tools: frozenset[str] = frozenset()
    max_tool_calls: int = Field(default=30, ge=1, le=1000)


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: ToolPolicy = Field(default_factory=ToolPolicy)
    network_access: Literal["disabled", "task_sources", "public_internet"] = "disabled"
    allowed_origins: tuple[str, ...] = ()
    local_compute: Literal["disabled", "isolated"] = "disabled"
    high_impact_mode: Literal["forbidden", "approval_required", "allowlisted"] = (
        "forbidden"
    )
    high_impact_allowed_actions: tuple[str, ...] = ()
    command_timeout_seconds: int = Field(default=120, ge=1, le=3600)


class ToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    intent_id: str | None = None
    solver_id: str
    tool_name: str
    risk: RiskLevel
    arguments: dict[str, Any]
    status: Literal[
        "proposed",
        "denied",
        "awaiting_approval",
        "approved",
        "running",
        "succeeded",
        "failed",
        "rejected",
    ] = "proposed"
    summary: str = ""
    artifact_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


__all__ = ["ExecutionPolicy", "RiskLevel", "ToolAction", "ToolPolicy"]
