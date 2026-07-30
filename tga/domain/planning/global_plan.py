"""Supervisor-owned versioned global intent plan."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.planning.intents import Intent


class GlobalPlan(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    version: int = Field(ge=1)
    status: Literal["draft", "active", "completed", "abandoned"] = "draft"
    intents: list[Intent] = Field(default_factory=list, max_length=1_024)
    created_by_solver_id: str | None = None
    created_at: str
    updated_at: str
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_intent_dag(self) -> "GlobalPlan":
        intents_by_id = {intent.id: intent for intent in self.intents}
        if len(intents_by_id) != len(self.intents):
            raise ValueError("GlobalPlan intent ids must be unique")
        for intent in self.intents:
            if intent.task_id != self.task_id:
                raise ValueError("GlobalPlan intent task ownership does not match")
            for dependency in intent.dependencies:
                if dependency.intent_id not in intents_by_id:
                    raise ValueError(f"GlobalPlan dependency does not exist: {dependency.intent_id}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(intent_id: str) -> None:
            if intent_id in visiting:
                raise ValueError("GlobalPlan dependency graph contains a cycle")
            if intent_id in visited:
                return
            visiting.add(intent_id)
            for dependency in intents_by_id[intent_id].dependencies:
                visit(dependency.intent_id)
            visiting.remove(intent_id)
            visited.add(intent_id)

        for intent_id in intents_by_id:
            visit(intent_id)
        return self


__all__ = ["GlobalPlan"]

