"""Immutable retrieval index snapshots."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.retrieval.corpus import OwnerScope


class IndexSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    owner: OwnerScope
    knowledge_base_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    document_hashes: dict[str, str] = Field(default_factory=dict)
    chunk_ids: tuple[str, ...] = ()
    chunking_version: str = Field(min_length=1, max_length=255)
    embedding_model: str | None = Field(default=None, max_length=255)
    index_version: int = Field(ge=1)
    created_at: str

    @model_validator(mode="after")
    def validate_snapshot(self) -> "IndexSnapshot":
        for name in ("knowledge_base_ids", "source_ids", "chunk_ids"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        invalid = [
            document_id for document_id, digest in self.document_hashes.items()
            if not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
        ]
        if invalid:
            raise ValueError("document_hashes must contain sha256 values")
        return self


class IndexBinding(BaseModel):
    """Persistent principal/purpose pin preventing silent snapshot switches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=512)
    owner: OwnerScope
    purpose: str = Field(min_length=1, max_length=255)
    index_snapshot_id: str = Field(min_length=1, max_length=255)
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


__all__ = ["IndexBinding", "IndexSnapshot"]
