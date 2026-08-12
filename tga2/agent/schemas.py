"""Structured outputs produced by role agents."""

from pydantic import BaseModel, ConfigDict, Field


class PlanIntentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=4000)
    priority: int = Field(default=50, ge=0, le=100)


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    intents: list[PlanIntentDraft] = Field(min_length=1, max_length=8)


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    statement: str = Field(min_length=1, max_length=8000)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    quote: str | None = Field(default=None, max_length=2000)


class WorkerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=8000)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=64)
    limitations: list[str] = Field(default_factory=list, max_length=32)


class FindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=8000)
    severity: str = "info"
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=64)
    remediation: str | None = Field(default=None, max_length=8000)


class ReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    feedback: str = Field(default="", max_length=4000)
    confirmed_claim_ids: list[str] = Field(default_factory=list, max_length=128)
    rejected_claim_ids: list[str] = Field(default_factory=list, max_length=128)
    findings: list[FindingDraft] = Field(default_factory=list, max_length=64)


class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executive_summary: str = Field(min_length=1, max_length=8000)
    methodology: list[str] = Field(default_factory=list, max_length=64)
    limitations: list[str] = Field(default_factory=list, max_length=64)


__all__ = [
    "ClaimDraft",
    "FindingDraft",
    "PlanDraft",
    "PlanIntentDraft",
    "ReportDraft",
    "ReviewDraft",
    "WorkerDraft",
]
