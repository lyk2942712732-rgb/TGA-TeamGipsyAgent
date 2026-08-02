"""Canonical execution-policy and controlled-action models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field



RiskLevel = Literal["passive", "active", "destructive"]
ActionKind = Literal["http", "tool", "workspace", "browser"]
ActionStatus = Literal[
    "proposed", "validated", "denied", "pending_approval", "approved", "queued",
    "running", "succeeded", "failed", "blocked", "cancelled", "rejected", "expired",
]


class TGAError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class NetworkExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    access: Literal["disabled", "task_sources", "public_internet", "custom"] = "disabled"
    interaction: Literal["observe", "interact"] = "observe"
    seed_origins: list[str] = Field(default_factory=list, max_length=128)
    custom_origins: list[str] = Field(default_factory=list, max_length=128)
    custom_domains: list[str] = Field(default_factory=list, max_length=128)
    custom_cidrs: list[str] = Field(default_factory=list, max_length=128)
    custom_ports: list[int] = Field(default_factory=list, max_length=1024)
    deny_private_networks: bool = True
    deny_loopback: bool = True
    deny_link_local: bool = True
    deny_cloud_metadata: bool = True
    rate_limit_per_minute: int = Field(default=30, ge=1, le=100_000)
    concurrency: int = Field(default=2, ge=1, le=128)
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)


class LocalComputeExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["disabled", "isolated"] = "disabled"
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    concurrency: int = Field(default=2, ge=1, le=128)
    network_inheritance: Literal["task_network_policy"] = "task_network_policy"


class HighImpactExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["forbidden", "approval_required", "allowlisted"] = "forbidden"
    allowed_actions: list[str] = Field(default_factory=list, max_length=64)


class ExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    preset: Literal["autonomous_ctf", "safe_observation", "offline_analysis", "custom"] = "offline_analysis"
    network: NetworkExecutionPolicy = Field(default_factory=NetworkExecutionPolicy)
    local_compute: LocalComputeExecutionPolicy = Field(default_factory=LocalComputeExecutionPolicy)
    high_impact: HighImpactExecutionPolicy = Field(default_factory=HighImpactExecutionPolicy)


class ActionEffect(BaseModel):
    """User-reviewable description of an Action's possible side effect."""

    model_config = {"extra": "forbid"}

    scope: Literal["none", "session", "workspace", "target"] = "none"
    persistence: Literal["none", "temporary", "persistent"] = "none"
    reversibility: Literal["not_applicable", "reversible", "uncertain", "irreversible"] = "not_applicable"
    category: Literal[
        "authentication", "submission", "file_write", "resource_create",
        "resource_modify", "resource_delete", "containment", "destructive_scan",
    ] = "submission"
    description: str = Field(default="No persistent side effect is declared.", min_length=1, max_length=500)


class ActionSpec(BaseModel):
    """The sole request shape accepted by a controlled executor (A -> B)."""

    id: str
    task_id: str
    solver_id: str
    intent_id: str | None = None
    local_plan_step_id: str | None = None
    execution_policy_snapshot_id: str | None = None
    solver_tool_policy_snapshot_id: str | None = None
    governed_action_id: str | None = None
    kind: ActionKind
    capability: str
    target: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    risk: RiskLevel
    strategy_card_id: str | None = None
    strategy_step_id: str | None = None
    expected_outcome: str = ""
    retry_reason: str = ""
    alternative_analysis: str = ""
    effect: ActionEffect = Field(default_factory=ActionEffect)
    input_id: str | None = None
    target_ref: str | None = None
    actual_target: str | None = None
    authorization: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """The sole execution result shape returned to the orchestration runtime."""

    action_id: str
    task_id: str
    solver_id: str
    status: ActionStatus
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    candidate_flags: list[str] = Field(default_factory=list)
    error: TGAError | None = None


__all__ = [
    "ActionEffect", "ActionKind", "ActionResult", "ActionSpec", "ActionStatus",
    "ExecutionPolicy", "HighImpactExecutionPolicy", "LocalComputeExecutionPolicy",
    "NetworkExecutionPolicy", "RiskLevel", "TGAError",
]
