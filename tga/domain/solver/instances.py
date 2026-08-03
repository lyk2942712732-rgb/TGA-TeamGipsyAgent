"""Durable task-local solver identities, separate from temporary runners."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.skills.models import SolverSkillSnapshot
from tga.domain.solver.budgets import SolverBudget
from tga.domain.capabilities.solver_binding import SolverKaliBinding
from tga.domain.solver.definitions import CompletionAuthority, OrchestrationRole
from tga.domain.solver.status import SolverInstanceStatus
from tga.domain.task.models import ModelSnapshot


class CapabilityBindingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host_capability_profile_id: str = Field(min_length=1, max_length=128)
    host_capability_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    kali: SolverKaliBinding | None = None
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SolverTimestamps(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    created_at: str
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None


class SolverInstance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    definition_id: str
    definition_version: str
    definition_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_solver_id: str | None = None
    assigned_intent_id: str | None = None
    orchestration_role: OrchestrationRole
    specialties: tuple[str, ...] = Field(min_length=1, max_length=32)
    status: SolverInstanceStatus = SolverInstanceStatus.CREATED
    model_snapshot: ModelSnapshot
    skill_snapshot: SolverSkillSnapshot | None = None
    capability_binding_snapshot: CapabilityBindingSnapshot
    budget: SolverBudget
    completion_authority: CompletionAuthority
    transcript_ref: str
    private_workspace_ref: str
    timestamps: SolverTimestamps

    @model_validator(mode="after")
    def validate_role_ownership(self) -> "SolverInstance":
        if self.orchestration_role == "supervisor" and self.parent_solver_id is not None:
            raise ValueError("supervisor SolverInstance cannot have parent_solver_id")
        if self.orchestration_role == "worker":
            if not self.parent_solver_id or not self.assigned_intent_id:
                raise ValueError("worker SolverInstance requires parent and assigned intent")
            if self.completion_authority == "task":
                raise ValueError("worker SolverInstance cannot own task completion")
        if self.skill_snapshot is not None:
            snapshot = self.skill_snapshot
            if snapshot.task_id != self.task_id or snapshot.solver_id != self.id:
                raise ValueError("SolverSkillSnapshot ownership does not match SolverInstance")
        return self


__all__ = ["CapabilityBindingSnapshot", "SolverInstance", "SolverTimestamps"]
