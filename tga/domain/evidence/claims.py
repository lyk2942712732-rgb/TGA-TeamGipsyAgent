"""Reviewable statements anchored to precise artifact locations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tga.domain.evidence.locators import EvidenceLocator


EvidenceClaimStatus = Literal["candidate", "confirmed", "rejected"]


class EvidenceClaim(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    statement: str = Field(min_length=1, max_length=8_000)
    artifact_id: str
    locator: EvidenceLocator
    status: EvidenceClaimStatus = "candidate"
    created_by_solver_id: str | None = None
    reviewed_by_solver_id: str | None = None
    created_at: str
    reviewed_at: str | None = None
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


__all__ = ["EvidenceClaim", "EvidenceClaimStatus"]

