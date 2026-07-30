"""Stable corpus documents and immutable revisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.retrieval.corpus import OwnerScope


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=255)
    knowledge_base_id: str = Field(min_length=1, max_length=255)
    owner: OwnerScope
    title: str = Field(min_length=1, max_length=1_000)
    canonical_uri: str | None = Field(default=None, max_length=4_096)
    current_revision_id: str | None = None
    status: Literal["active", "failed", "archived"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str | None = None


class DocumentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    document_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=255)
    owner: OwnerScope
    revision: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    extraction_status: Literal["pending", "parsed", "failed"] = "pending"
    error: str | None = Field(default=None, max_length=4_000)
    media_type: str | None = Field(default=None, max_length=255)
    byte_size: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


__all__ = ["CorpusDocument", "DocumentRevision"]
