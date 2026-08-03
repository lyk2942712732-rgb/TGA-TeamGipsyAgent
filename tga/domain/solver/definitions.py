"""Reusable, configuration-backed solver definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tga.domain.capabilities.solver_binding import (
    HostCapabilityOverrides,
    SolverKaliBinding,
)
from tga.domain.solver.budgets import SolverBudget
from tga.modes import TaskMode


OrchestrationRole = Literal["supervisor", "worker", "reviewer", "reporter"]
CompletionAuthority = Literal["none", "worker_only", "task"]


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
    host_capability_profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    host_capability_overrides: HostCapabilityOverrides = Field(
        default_factory=HostCapabilityOverrides
    )
    kali: SolverKaliBinding | None = None
    accepted_intent_kinds: tuple[str, ...] = Field(min_length=1, max_length=64)
    output_contract: SolverOutputContract
    default_budget: SolverBudget
    completion_authority: CompletionAuthority = "none"
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "specialties", "supported_modes", "supported_subtypes", "default_skill_tags",
        "required_skill_names",
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
        if self.orchestration_role in {"reviewer", "reporter"} and self.kali is not None:
            raise ValueError(f"{self.orchestration_role} SolverDefinition cannot bind Kali")
        return self

    def supports(self, *, mode: TaskMode, subtype: str | None = None) -> bool:
        if mode not in self.supported_modes:
            return False
        return not self.supported_subtypes or not subtype or subtype in self.supported_subtypes


__all__ = [
    "CompletionAuthority", "OrchestrationRole", "SolverDefinition",
    "SolverOutputContract",
]
