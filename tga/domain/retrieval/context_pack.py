"""Bounded retrieved context; references never become facts by retrieval alone."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.retrieval.chunks import ChunkLocator
from tga.domain.retrieval.corpus import OwnerScope, RetrievalChannel, TrustLevel


class RetrievedContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hit_id: str
    owner: OwnerScope
    chunk_id: str
    knowledge_base_id: str
    source_id: str
    document_id: str
    revision_id: str
    channel: RetrievalChannel
    label: str
    trust_level: TrustLevel
    content: str
    locator: ChunkLocator
    retrieval_score: float
    rerank_score: float
    rank: int
    token_count: int = Field(ge=1)
    truncated: bool = False
    safety_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_run_id: str
    owner: OwnerScope
    index_snapshot_id: str
    task_id: str | None = None
    solver_id: str | None = None
    intent_id: str | None = None
    items: tuple[RetrievedContextItem, ...] = ()
    total_tokens: int = Field(default=0, ge=0)
    max_context_tokens: int = Field(ge=1)
    truncated: bool = False
    created_at: str

    @model_validator(mode="after")
    def validate_budget(self) -> "RetrievedContextPack":
        if self.total_tokens != sum(item.token_count for item in self.items):
            raise ValueError("ContextPack token total must match selected items")
        if self.total_tokens > self.max_context_tokens:
            raise ValueError("ContextPack exceeds max_context_tokens")
        return self


__all__ = ["RetrievedContextItem", "RetrievedContextPack"]
