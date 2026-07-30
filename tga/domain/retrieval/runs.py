"""Auditable retrieval requests, effective runs, and ranked hits."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.retrieval.corpus import OwnerScope, RetrievalChannel


RetrievalMethod = Literal["vector", "keyword", "hybrid"]


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner: OwnerScope
    task_id: str | None = None
    solver_id: str | None = None
    intent_id: str | None = None
    query: str = Field(min_length=1, max_length=8_000)
    rewritten_query: str | None = Field(default=None, max_length=8_000)
    index_snapshot_id: str
    filters: dict[str, Any] = Field(default_factory=dict)
    method: RetrievalMethod = "hybrid"
    knowledge_base_ids: tuple[str, ...] = ()
    channels: tuple[RetrievalChannel, ...] = ("reference",)
    created_at: str

    @model_validator(mode="after")
    def validate_principal(self) -> "RetrievalRequest":
        _validate_principal(self.owner, self.task_id, self.solver_id)
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("Retrieval channels must be unique")
        if len(self.knowledge_base_ids) != len(set(self.knowledge_base_ids)):
            raise ValueError("KnowledgeBase ids must be unique")
        return self


class RetrievalRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner: OwnerScope
    task_id: str | None = None
    solver_id: str | None = None
    intent_id: str | None = None
    query: str
    rewritten_query: str
    index_snapshot_id: str
    filters: dict[str, Any] = Field(default_factory=dict)
    requested_method: RetrievalMethod
    method: RetrievalMethod
    knowledge_base_ids: tuple[str, ...] = ()
    channels: tuple[RetrievalChannel, ...] = ()
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str

    @model_validator(mode="after")
    def validate_principal(self) -> "RetrievalRun":
        _validate_principal(self.owner, self.task_id, self.solver_id)
        return self


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    retrieval_run_id: str
    owner: OwnerScope
    chunk_id: str
    retrieval_score: float = Field(ge=0)
    rerank_score: float = Field(ge=0)
    rank: int = Field(ge=1)
    selected_for_context: bool = False
    safety_flags: tuple[str, ...] = ()
    created_at: str


def _validate_principal(
    owner: OwnerScope, task_id: str | None, solver_id: str | None
) -> None:
    if solver_id is not None and task_id is None:
        raise ValueError("solver_id audit identity requires task_id")
    if owner.scope == "task" and owner.task_id != task_id:
        raise ValueError("task retrieval principal must record its task_id")
    if owner.scope == "solver" and (
        owner.task_id != task_id or owner.solver_id != solver_id
    ):
        raise ValueError("solver retrieval principal must record task_id and solver_id")


__all__ = ["RetrievalHit", "RetrievalMethod", "RetrievalRequest", "RetrievalRun"]
