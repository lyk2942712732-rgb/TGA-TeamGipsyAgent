"""Durable execution attempts for task-local Solver identities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SolverRunState = Literal[
    "queued",
    "leased",
    "running",
    "waiting_approval",
    "retry_queued",
    "completed",
    "failed",
    "cancelled",
    "expired",
]

TERMINAL_SOLVER_RUN_STATES = frozenset({"completed", "failed", "cancelled", "expired"})


class SolverRun(BaseModel):
    """One auditable attempt to execute a Solver assignment or role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    solver_id: str
    assignment_id: str | None = None
    intent_id: str | None = None
    orchestration_role: Literal["supervisor", "worker", "reviewer", "reporter"]
    state: SolverRunState = "queued"
    attempt: int = Field(default=1, ge=1)
    lease_owner: str | None = None
    fencing_token: int = Field(default=0, ge=0)
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "SolverRun":
        leased = self.state in {"leased", "running"}
        lease_values = (self.lease_owner, self.lease_expires_at, self.heartbeat_at)
        if leased and (not all(lease_values) or self.fencing_token < 1):
            raise ValueError("leased SolverRun state requires a complete fenced lease")
        if self.state == "running" and not self.started_at:
            raise ValueError("running SolverRun requires started_at")
        if self.state in TERMINAL_SOLVER_RUN_STATES and not self.finished_at:
            raise ValueError("terminal SolverRun requires finished_at")
        if self.orchestration_role == "worker" and (
            not self.assignment_id or not self.intent_id
        ):
            raise ValueError("Worker SolverRun requires assignment_id and intent_id")
        return self


__all__ = ["SolverRun", "SolverRunState", "TERMINAL_SOLVER_RUN_STATES"]
