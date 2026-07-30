"""Explicit conflicts between knowledge items."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class KnowledgeConflict(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    knowledge_item_ids: list[str] = Field(min_length=2, max_length=64)
    description: str = Field(min_length=1, max_length=4_000)
    status: Literal["open", "resolved"] = "open"
    resolution: str = Field(default="", max_length=4_000)
    resolved_by_solver_id: str | None = None
    created_at: str
    resolved_at: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_conflict(self) -> "KnowledgeConflict":
        if len(self.knowledge_item_ids) != len(set(self.knowledge_item_ids)):
            raise ValueError("KnowledgeConflict item ids must be unique")
        if self.status == "resolved" and (
            not self.resolution.strip() or not self.resolved_by_solver_id or not self.resolved_at
        ):
            raise ValueError("resolved KnowledgeConflict requires resolution provenance")
        return self


__all__ = ["KnowledgeConflict"]

