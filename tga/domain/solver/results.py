"""Structured worker output; workers recommend rather than complete tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.planning.proposals import IntentProposal
from tga.domain.solver.budgets import SolverBudgetUsage
from tga.domain.solver.status import WorkerResultStatus


class WorkerCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    not_covered: tuple[str, ...] = Field(default_factory=tuple, max_length=256)


class SolverError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    intent_id: str
    status: WorkerResultStatus
    summary: str = Field(min_length=1, max_length=8_000)
    artifact_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    candidate_evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    candidate_knowledge_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    coverage: WorkerCoverage = Field(default_factory=WorkerCoverage)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    recommended_next_intents: tuple[IntentProposal, ...] = Field(default_factory=tuple, max_length=64)
    budget_usage: SolverBudgetUsage = Field(default_factory=SolverBudgetUsage)
    errors: tuple[SolverError, ...] = Field(default_factory=tuple, max_length=256)

    @model_validator(mode="after")
    def validate_recommendations(self) -> "WorkerResult":
        for proposal in self.recommended_next_intents:
            if proposal.task_id != self.task_id or proposal.proposed_by_solver_id != self.solver_id:
                raise ValueError("WorkerResult intent proposal ownership does not match")
        return self


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    worker_result_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    status: Literal["confirmed", "rejected", "needs_more_evidence"]
    confirmed_evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    confirmed_knowledge_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    confirmed_finding_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    rejected_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    contradictions: tuple[str, ...] = Field(default_factory=tuple, max_length=256)


class ReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    solver_id: str
    review_result_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_024)
    status: Literal["completed", "failed", "blocked"]
    summary: str = Field(min_length=1, max_length=8_000)
    report_artifact_id: str | None = None
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=256)


__all__ = [
    "ReportResult", "ReviewResult", "SolverError", "WorkerCoverage", "WorkerResult",
]
