"""Solver/Intent-scoped approval request."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.governance.models import ActionEffect


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    solver_id: str
    intent_id: str | None = None
    action_id: str
    governed_action_id: str
    reason: str
    risk: str
    effect: ActionEffect
    alternatives: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    expires_at: str
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"] = "pending"
    created_at: str
    updated_at: str


__all__ = ["ApprovalRequest"]
