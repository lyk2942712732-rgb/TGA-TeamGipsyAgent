"""Durable state for projecting one immutable Artifact into Retrieval."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ArtifactIndexingStatus = Literal["pending", "indexing", "indexed", "failed"]


class ArtifactIndexingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    allowed_media_types: tuple[str, ...] = (
        "application/json",
        "application/pdf",
        "application/xml",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/xml",
    )
    denied_artifact_kinds: tuple[str, ...] = ()
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_chunks: int = Field(default=2_000, ge=1, le=100_000)
    max_total_tokens: int = Field(default=500_000, ge=1, le=10_000_000)
    auto_refresh_context_binding: bool = True
    index_on_creation: bool = True


class ArtifactIndexProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=255)
    artifact_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    task_id: str = Field(min_length=1, max_length=128)
    status: ArtifactIndexingStatus
    source_id: str | None = Field(default=None, max_length=255)
    document_id: str | None = Field(default=None, max_length=255)
    revision_id: str | None = Field(default=None, max_length=255)
    chunk_ids: tuple[str, ...] = ()
    snapshot_id: str | None = Field(default=None, max_length=255)
    binding_updated: bool = False
    attempt: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=4_000)
    retryable: bool = False
    created_at: str
    updated_at: str


__all__ = [
    "ArtifactIndexProjection", "ArtifactIndexingPolicy", "ArtifactIndexingStatus",
]
