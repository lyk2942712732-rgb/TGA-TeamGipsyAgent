"""Worker proposals are inputs to, not mutations of, the global plan."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.planning.intents import Intent


class IntentProposal(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    proposed_by_solver_id: str
    base_global_plan_version: int = Field(ge=1)
    intent: Intent
    rationale: str = Field(min_length=1, max_length=4_000)
    status: Literal["pending", "accepted", "rejected", "superseded"] = "pending"
    created_at: str
    reviewed_at: str | None = None
    reviewed_by_solver_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ownership_and_review(self) -> "IntentProposal":
        if self.intent.task_id != self.task_id:
            raise ValueError("proposed intent task ownership does not match")
        if self.status != "pending" and not self.reviewed_by_solver_id:
            raise ValueError("reviewed proposal requires reviewed_by_solver_id")
        return self


__all__ = ["IntentProposal"]

