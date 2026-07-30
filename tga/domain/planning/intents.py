"""Intent nodes and dependency edges for the global-plan DAG."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


IntentStatus = Literal[
    "proposed", "pending", "ready", "assigned", "running", "awaiting_approval",
    "reviewing", "blocked", "completed", "failed", "cancelled"
]


class IntentDependency(BaseModel):
    model_config = {"extra": "forbid"}

    intent_id: str
    required_status: Literal["completed"] = "completed"
    condition: str = Field(default="", max_length=1_000)


class Intent(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    kind: str = Field(default="general", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=4_000)
    dependencies: list[IntentDependency] = Field(default_factory=list, max_length=128)
    status: IntentStatus = "proposed"
    assigned_solver_id: str | None = None
    budget: dict[str, int] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-1_000, le=1_000)
    created_at: str
    updated_at: str
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("budget")
    @classmethod
    def validate_budget(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or amount < 0 for key, amount in value.items()):
            raise ValueError("intent budget keys must be non-empty and values non-negative")
        return value

    @model_validator(mode="after")
    def validate_dependencies(self) -> "Intent":
        dependency_ids = [dependency.intent_id for dependency in self.dependencies]
        if self.id in dependency_ids:
            raise ValueError("intent cannot depend on itself")
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("intent dependencies must be unique")
        return self


__all__ = ["Intent", "IntentDependency", "IntentStatus"]
