"""Role outputs and bounded Runtime-built intelligence packets."""

from typing import Any, Literal

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
    verdict: Literal["pass", "retry", "reject", "needs_user"]
    reason_codes: list[
        Literal[
            "insufficient_evidence",
            "invalid_locator",
            "unsupported_claim",
            "contradiction",
            "incomplete_objective",
            "policy_violation",
        ]
    ] = Field(default_factory=list, max_length=16)
    feedback: str = Field(default="", max_length=4000)
    confirmed_claim_ids: list[str] = Field(default_factory=list, max_length=128)
    rejected_claim_ids: list[str] = Field(default_factory=list, max_length=128)
    findings: list[FindingDraft] = Field(default_factory=list, max_length=64)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: dict[str, Any]
    artifact_sha256: str
    artifact_kind: str
    source_tool: str | None = None
    cited_excerpt: str
    locator_valid: bool


class ReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_objective: str
    current_intent: dict[str, Any]
    worker_result: dict[str, Any]
    evidence: list[EvidencePacket]
    success_criteria: list[str]


class SituationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_objective: str
    authorization: dict[str, Any]
    plan: dict[str, Any]
    current_intent: dict[str, Any]
    worker_result: dict[str, Any] | None = None
    review_result: dict[str, Any] | None = None
    confirmed_findings: list[dict[str, Any]] = Field(default_factory=list)
    failed_attempts: list[dict[str, Any]] = Field(default_factory=list)
    user_interventions: list[dict[str, Any]] = Field(default_factory=list)
    remaining_budget: dict[str, int | float]
    tool_health: dict[str, Any]


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "next_intent", "revise_plan", "ask_user", "finish", "fail"]
    reason: str = Field(min_length=1, max_length=4000)
    feedback: str | None = Field(default=None, max_length=4000)
    new_intents: list[PlanIntentDraft] = Field(default_factory=list, max_length=8)
    user_question: str | None = Field(default=None, max_length=4000)


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
    "ReviewPacket",
    "SituationPacket",
    "SupervisorDecision",
    "WorkerDraft",
]
