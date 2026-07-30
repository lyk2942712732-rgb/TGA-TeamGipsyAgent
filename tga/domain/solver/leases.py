"""Fenced durable leases for temporary Solver runners and task orchestrators."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SolverLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    owner_id: str
    fencing_token: int = Field(ge=1)
    expires_at: str
    renewed_at: str


class TaskOrchestratorLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    owner_id: str
    fencing_token: int = Field(ge=1)
    expires_at: str
    renewed_at: str


__all__ = ["SolverLease", "TaskOrchestratorLease"]
