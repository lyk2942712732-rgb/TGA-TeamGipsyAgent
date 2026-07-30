"""Mode-specific bounded team templates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.modes import TaskMode


class SpawnRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: Literal["task_start", "intent_ready", "review_required", "report_required"]
    definition_id: str
    max_instances: int = Field(default=1, ge=1, le=32)


class TeamCompletionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supervisor_decides: bool = True
    require_reviewer: bool = True
    require_reporter: bool = True
    require_all_required_intents_terminal: bool = True


class TeamTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TaskMode
    supervisor_definition_id: str
    required_solver_definition_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    available_solver_definition_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    reviewer_definition_id: str
    reporter_definition_id: str
    spawn_rules: tuple[SpawnRule, ...] = Field(default_factory=tuple, max_length=64)
    max_active_workers: int = Field(ge=1, le=2)
    max_total_solvers: int = Field(ge=1, le=64)
    completion_policy: TeamCompletionPolicy
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_definition_sets(self) -> "TeamTemplate":
        required = set(self.required_solver_definition_ids)
        available = set(self.available_solver_definition_ids)
        if len(required) != len(self.required_solver_definition_ids):
            raise ValueError("required solver definitions must be unique")
        if len(available) != len(self.available_solver_definition_ids):
            raise ValueError("available solver definitions must be unique")
        if not required.issubset(available):
            raise ValueError("required solver definitions must also be available")
        minimum = 1 + len(required) + int(self.completion_policy.require_reviewer) + int(
            self.completion_policy.require_reporter
        )
        if self.max_total_solvers < minimum:
            raise ValueError("max_total_solvers cannot fit required team roles")
        return self


__all__ = ["SpawnRule", "TeamCompletionPolicy", "TeamTemplate"]
