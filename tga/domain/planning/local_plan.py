"""A solver-private plan for one assigned intent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LocalPlanStep(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    solver_id: str
    intent_id: str
    description: str = Field(min_length=1, max_length=4_000)
    status: Literal["pending", "running", "completed", "failed", "blocked", "skipped"] = "pending"
    order: int = Field(default=0, ge=0)
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=256)
    provenance: dict[str, Any] = Field(default_factory=dict)


class LocalPlan(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    solver_id: str
    intent_id: str
    version: int = Field(ge=1)
    status: Literal["draft", "active", "completed", "abandoned"] = "draft"
    steps: list[LocalPlanStep] = Field(default_factory=list, max_length=512)
    created_at: str
    updated_at: str
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_step_ownership(self) -> "LocalPlan":
        ids: list[str] = []
        for step in self.steps:
            if step.solver_id != self.solver_id or step.intent_id != self.intent_id:
                raise ValueError("LocalPlan step ownership must match solver_id and intent_id")
            ids.append(step.id)
        if len(ids) != len(set(ids)):
            raise ValueError("LocalPlan step ids must be unique")
        return self


__all__ = ["LocalPlan", "LocalPlanStep"]

