"""Proposals to promote solver/intent knowledge into a wider scope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.knowledge.scopes import KnowledgeScope


class KnowledgePromotionProposal(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    knowledge_item_id: str
    from_scope: KnowledgeScope
    from_target_id: str | None = None
    to_scope: KnowledgeScope
    to_target_id: str | None = None
    proposed_by_solver_id: str
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=256)
    status: Literal["pending", "accepted", "rejected", "superseded"] = "pending"
    created_at: str
    reviewed_at: str | None = None
    reviewed_by_solver_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_promotion(self) -> "KnowledgePromotionProposal":
        if self.from_scope == self.to_scope and self.from_target_id == self.to_target_id:
            raise ValueError("knowledge promotion must change scope or ownership")
        if self.from_scope == "task":
            raise ValueError("task-scoped knowledge cannot be promoted further")
        if self.from_scope != "task" and not self.from_target_id:
            raise ValueError("non-task source scope requires from_target_id")
        if self.to_scope == "task" and self.to_target_id is not None:
            raise ValueError("task promotion target must not have to_target_id")
        if self.to_scope != "task" and not self.to_target_id:
            raise ValueError("non-task target scope requires to_target_id")
        if self.status != "pending" and not self.reviewed_by_solver_id:
            raise ValueError("reviewed knowledge promotion requires reviewer")
        return self


__all__ = ["KnowledgePromotionProposal"]

