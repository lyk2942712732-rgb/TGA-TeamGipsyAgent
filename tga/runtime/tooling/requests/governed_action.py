"""Normalized action admitted to the governed lifecycle."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.governance.models import ActionEffect
from tga.runtime.tooling.requests.action_context import ActionContext


ToolClass = Literal["control", "resource_read", "execution", "retrieval"]
GovernedActionStatus = Literal[
    "proposed", "validated", "denied", "pending_approval", "approved",
    "queued", "running", "succeeded", "failed", "blocked", "rejected",
    "expired", "cancelled",
]


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    code: str | None = None
    reason: str
    requires_approval: bool = False
    policy_snapshot_ids: tuple[str, ...] = ()


class GovernedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    context: ActionContext
    provider_tool_name: str
    tool_call_id: str
    tool_class: ToolClass
    capability: str
    execution_profile_id: str | None = None
    sandbox_config_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normalized_arguments: dict[str, Any] = Field(default_factory=dict)
    resolved_target: str | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=500)
    expected_outcome: str = Field(default="", max_length=500)
    retry_reason: str | None = Field(default=None, max_length=500)
    alternative_analysis: str | None = Field(default=None, max_length=500)
    risk: Literal["passive", "active", "destructive"]
    effect: ActionEffect
    authorization: AuthorizationDecision
    attempt: int = Field(default=1, ge=1, le=1_000_000)
    idempotency_key: str | None = None
    semantic_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    resource_lock_key: str | None = None
    status: GovernedActionStatus = "proposed"
    created_at: str
    updated_at: str


__all__ = [
    "AuthorizationDecision", "GovernedAction", "GovernedActionStatus", "ToolClass",
]
