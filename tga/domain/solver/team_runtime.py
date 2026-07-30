"""Durable task-level orchestration state, independent of runner processes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TaskOrchestratorStatus = Literal[
    "created", "running", "paused", "awaiting_input", "blocked",
    "completed", "failed", "cancelled",
]


class TeamRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    version: int = Field(default=1, ge=1)
    team_template_sha256: str
    supervisor_solver_id: str | None = None
    status: TaskOrchestratorStatus = "created"
    max_active_workers: int = Field(default=1, ge=1, le=2)
    max_total_solvers: int = Field(ge=1, le=64)
    merged_worker_result_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=4_096)
    review_result_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=4_096)
    report_result_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=4_096)
    completion_proposal: dict[str, Any] | None = None
    created_at: str
    updated_at: str


__all__ = ["TaskOrchestratorStatus", "TeamRuntimeState"]
