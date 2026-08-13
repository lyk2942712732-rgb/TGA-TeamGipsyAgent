"""Role outputs and bounded Runtime-built intelligence packets."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def structured_output_prompt(
    base_prompt: str,
    instruction: str,
    expected: type[BaseModel],
) -> str:
    """Build the explicit schema prompt required by OpenAI-compatible JSON mode.

    ``with_structured_output(..., method="json_mode")`` validates the response,
    but compatible providers do not necessarily receive the Pydantic schema
    automatically. Supplying it here prevents models from guessing field names
    such as ``intent`` instead of the required ``intents`` array.
    """

    schema = json.dumps(
        expected.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n\n".join(
        part
        for part in (
            base_prompt.strip(),
            instruction.strip(),
            (
                "Return exactly one JSON object and no Markdown. Follow this JSON "
                f"Schema exactly; do not rename fields or add fields:\n{schema}"
            ),
        )
        if part
    )


class PlanIntentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=4000)
    priority: int = Field(default=50, ge=0, le=100)
    success_criteria: list[str] = Field(min_length=1, max_length=8)
    expected_evidence: list[str] = Field(min_length=1, max_length=8)

    @field_validator("success_criteria", "expected_evidence")
    @classmethod
    def reject_blank_acceptance_items(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("Intent acceptance items cannot be blank")
        return cleaned


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


class CriterionAssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_index: int = Field(ge=0, le=7)
    status: Literal["met", "unmet", "blocked"]
    artifact_ids: list[str] = Field(default_factory=list, max_length=32)
    note: str = Field(default="", max_length=2000)


class WorkerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=8000)
    completion_status: Literal["completed", "incomplete", "blocked", "needs_user"]
    criterion_assessments: list[CriterionAssessmentDraft] = Field(max_length=8)
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
    criterion_results: list["CriterionReviewDraft"] = Field(max_length=8)

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


class CriterionReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion_index: int = Field(ge=0, le=7)
    status: Literal["verified", "not_verified"]
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=64)
    reason: str = Field(default="", max_length=2000)


class ReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_objective: str
    current_intent: dict[str, Any]
    worker_result: dict[str, Any]
    evidence: list[EvidencePacket]
    task_success_criteria: list[str]
    intent_success_criteria: list[str]
    expected_evidence: list[str]
    stop_conditions: list[str]


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
    "CriterionAssessmentDraft",
    "CriterionReviewDraft",
    "FindingDraft",
    "PlanDraft",
    "PlanIntentDraft",
    "ReportDraft",
    "ReviewDraft",
    "ReviewPacket",
    "SituationPacket",
    "SupervisorDecision",
    "WorkerDraft",
    "structured_output_prompt",
]
