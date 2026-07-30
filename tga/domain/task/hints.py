"""Unverified task hints and their review lifecycle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


HintStatus = Literal["unreviewed", "considered", "verified", "rejected"]
TaskScope = Literal["task", "solver", "intent"]


class TaskHint(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    content: str = Field(min_length=1, max_length=8_000)
    source: str = Field(min_length=1, max_length=512)
    status: HintStatus = "unreviewed"
    scope: TaskScope = "task"
    target_id: str | None = None
    created_at: str
    reviewed_at: str | None = None
    reviewed_by_solver_id: str | None = None
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope_target(self) -> "TaskHint":
        if self.scope == "task" and self.target_id is not None:
            raise ValueError("task-scoped hint must not have target_id")
        if self.scope != "task" and not self.target_id:
            raise ValueError(f"{self.scope}-scoped hint requires target_id")
        if self.status in {"considered", "verified", "rejected"} and not self.reviewed_by_solver_id:
            raise ValueError("reviewed hint status requires reviewed_by_solver_id")
        return self


__all__ = ["HintStatus", "TaskHint", "TaskScope"]

