"""Phase-9 command DTOs owned by the application boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommandDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterventionRequest(CommandDTO):
    kind: Literal["hint", "instruction", "constraint", "priority_change", "answer", "approval"]
    content: str = Field(min_length=1, max_length=8_000)
    scope: Literal["task", "solver", "intent"] = "task"
    target_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_target(self) -> "InterventionRequest":
        if self.scope == "task" and self.target_id is not None:
            raise ValueError("task-scoped intervention cannot have target_id")
        if self.scope != "task" and not self.target_id:
            raise ValueError(f"{self.scope}-scoped intervention requires target_id")
        return self


class ApprovalDecisionRequest(CommandDTO):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=1_000)


class SolverControlRequest(CommandDTO):
    action: Literal["pause", "resume", "cancel"]
    reason: str = Field(default="operator_request", min_length=1, max_length=500)


class IntentRetryRequest(CommandDTO):
    reason: str = Field(default="operator_retry", min_length=1, max_length=500)


class CommandResponse(CommandDTO):
    schema_version: Literal[6] = 6
    task_id: str
    accepted: bool
    status: str
    scheduled: bool = False
    reason: str | None = None


__all__ = [
    "ApprovalDecisionRequest", "CommandResponse", "IntentRetryRequest",
    "InterventionRequest", "SolverControlRequest",
]
