"""Conclusions supported by reviewable evidence claims."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.evidence.claims import EvidenceClaim


FindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class Finding(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=8_000)
    target: str | None = Field(default=None, max_length=4_096)
    severity: Severity
    status: FindingStatus = "candidate"
    evidence_claims: list[EvidenceClaim] = Field(default_factory=list, max_length=256)
    reproduction_steps: list[str] = Field(default_factory=list, max_length=256)
    remediation: str | None = Field(default=None, max_length=8_000)
    created_by_solver_id: str | None = None
    created_at: str
    reviewed_at: str | None = None
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence(self) -> "Finding":
        claim_ids: list[str] = []
        for claim in self.evidence_claims:
            if claim.task_id != self.task_id:
                raise ValueError("Finding and EvidenceClaim task ownership must match")
            claim_ids.append(claim.id)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Finding EvidenceClaim references must be unique")
        if self.status == "confirmed" and not any(
            claim.status == "confirmed" for claim in self.evidence_claims
        ):
            raise ValueError("confirmed Finding requires at least one confirmed EvidenceClaim")
        return self

    @property
    def evidence_claim_ids(self) -> list[str]:
        return [claim.id for claim in self.evidence_claims]


__all__ = ["Finding", "FindingStatus", "Severity"]

