"""Explicit intent assignment lifecycle for durable solver identities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from tga.domain.planning.intents import Intent
from tga.domain.skills.models import SolverSkillSnapshot
from tga.domain.solver.budgets import SolverBudget
from tga.domain.solver.instances import CapabilityBindingSnapshot


class SolverAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    solver_id: str
    intent_id: str
    assigned_by_solver_id: str
    intent: Intent
    allowed_resources: tuple[str, ...] = ()
    relevant_knowledge_ids: tuple[str, ...] = ()
    relevant_evidence_claim_ids: tuple[str, ...] = ()
    skill_snapshot: SolverSkillSnapshot | None = None
    capability_binding_snapshot: CapabilityBindingSnapshot
    budget: SolverBudget
    allowed_control_tools: tuple[str, ...] = (
        "update_local_plan", "propose_knowledge", "submit_worker_result",
    )
    attempt: int = 1
    status: Literal["proposed", "accepted", "released", "completed", "cancelled"] = "proposed"
    assigned_at: str
    accepted_at: str | None = None
    finished_at: str | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "SolverAssignment":
        if self.intent.id != self.intent_id or self.intent.task_id != self.task_id:
            raise ValueError("assignment Intent ownership does not match")
        if self.skill_snapshot is not None and (
            self.skill_snapshot.task_id != self.task_id
            or self.skill_snapshot.solver_id != self.solver_id
            or self.skill_snapshot.intent_id != self.intent_id
        ):
            raise ValueError("assignment Skill snapshot ownership does not match")
        if self.status in {"accepted", "completed"} and not self.accepted_at:
            raise ValueError("accepted assignment state requires accepted_at")
        if self.status in {"released", "completed", "cancelled"} and not self.finished_at:
            raise ValueError("terminal assignment state requires finished_at")
        if self.attempt < 1:
            raise ValueError("assignment attempt must be positive")
        return self


__all__ = ["SolverAssignment"]
