"""Reusable, configuration-backed solver definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tga.domain.solver.budgets import SolverBudget
from tga.modes import TaskMode


OrchestrationRole = Literal["supervisor", "worker", "reviewer", "reporter"]
CompletionAuthority = Literal["none", "worker_only", "task"]
ToolGroup = Literal["control", "resource_read", "execution", "retrieval"]


class SolverOutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    required_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=64)

    @field_validator("required_fields")
    @classmethod
    def validate_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("output contract fields must be non-empty and unique")
        return value


class SolverDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    orchestration_role: OrchestrationRole
    specialties: tuple[str, ...] = Field(min_length=1, max_length=32)
    supported_modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    supported_subtypes: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    system_prompt_template: str = Field(min_length=1, max_length=32_000)
    default_skill_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    required_skill_names: tuple[str, ...] = Field(default_factory=tuple, max_length=3)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    allowed_tool_groups: tuple[ToolGroup, ...] = Field(default_factory=tuple, max_length=4)
    sandbox_profile_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
    )
    sandbox_required: bool = False
    tool_policy_profile: str = Field(min_length=1, max_length=128)
    accepted_intent_kinds: tuple[str, ...] = Field(min_length=1, max_length=64)
    output_contract: SolverOutputContract
    default_budget: SolverBudget
    completion_authority: CompletionAuthority = "none"
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "specialties", "supported_modes", "supported_subtypes", "default_skill_tags",
        "required_skill_names", "required_capabilities", "allowed_tool_groups",
        "accepted_intent_kinds",
    )
    @classmethod
    def validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("solver definition list values must be unique")
        return value

    @model_validator(mode="after")
    def validate_completion_authority(self) -> "SolverDefinition":
        if self.orchestration_role == "worker" and self.completion_authority == "task":
            raise ValueError("Worker cannot own task completion authority")
        if self.completion_authority == "worker_only" and self.orchestration_role != "worker":
            raise ValueError("worker_only completion authority is valid only for workers")
        if self.sandbox_required and not self.sandbox_profile_id:
            raise ValueError("sandbox_required SolverDefinition must assign a sandbox profile")
        if self.sandbox_profile_id and "execution" not in self.allowed_tool_groups:
            raise ValueError("sandbox profile requires the execution tool group")
        return self

    def supports(self, *, mode: TaskMode, subtype: str | None = None) -> bool:
        if mode not in self.supported_modes:
            return False
        return not self.supported_subtypes or not subtype or subtype in self.supported_subtypes


__all__ = [
    "CompletionAuthority", "OrchestrationRole", "SolverDefinition",
    "SolverOutputContract", "ToolGroup",
]
