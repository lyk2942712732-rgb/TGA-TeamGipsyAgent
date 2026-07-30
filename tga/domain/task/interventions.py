"""Typed user interventions received after task creation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.task.hints import TaskScope


InterventionKind = Literal[
    "hint", "instruction", "constraint", "priority_change", "answer", "approval"
]


class UserIntervention(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    kind: InterventionKind
    content: str = Field(min_length=1, max_length=8_000)
    scope: TaskScope = "task"
    target_id: str | None = None
    created_at: str
    actor_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope_target(self) -> "UserIntervention":
        if self.scope == "task" and self.target_id is not None:
            raise ValueError("task-scoped intervention must not have target_id")
        if self.scope != "task" and not self.target_id:
            raise ValueError(f"{self.scope}-scoped intervention requires target_id")
        return self


__all__ = ["InterventionKind", "UserIntervention"]

