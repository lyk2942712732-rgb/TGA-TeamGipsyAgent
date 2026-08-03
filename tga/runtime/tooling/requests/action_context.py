"""Authoritative tool-call ownership injected by a Solver runner."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    run_id: str | None = None
    run_owner_id: str | None = None
    run_fencing_token: int | None = Field(default=None, ge=1)
    intent_id: str | None = None
    local_plan_step_id: str | None = None
    orchestration_role: Literal["supervisor", "worker", "reviewer", "reporter"]
    solver_definition_id: str
    execution_policy_snapshot_id: str = Field(min_length=1, max_length=256)
    solver_capability_snapshot_id: str = Field(min_length=1, max_length=256)
    skill_snapshot_id: str | None = Field(default=None, max_length=256)
    attempt: int = Field(default=1, ge=1, le=1_000_000)
    created_at: str


__all__ = ["ActionContext"]
