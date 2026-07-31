"""Bounded search projections for current immutable artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ExtractionStatus = Literal[
    "not_requested", "blocked_out_of_scope", "failed", "extracted",
]


class ArtifactSegment(BaseModel):
    model_config = {"extra": "forbid"}

    ref: str
    heading: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=8000)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)


class ArtifactIndex(BaseModel):
    """Searchable, non-authoritative projection of an immutable Artifact."""

    model_config = {"extra": "forbid"}

    artifact_id: str
    task_id: str
    document_type: str
    extraction_status: ExtractionStatus
    summary: str = Field(default="", max_length=2400)
    segments: list[ArtifactSegment] = Field(default_factory=list, max_length=128)
    created_at: str


__all__ = ["ArtifactIndex", "ArtifactSegment", "ExtractionStatus"]
