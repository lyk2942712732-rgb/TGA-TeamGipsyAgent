"""Canonical locations for legacy session, solver and strategy models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SessionStatus = Literal["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"]
SolverStatus = Literal["starting", "running", "waiting", "completed", "failed", "cancelled"]
SolverRole = Literal["recon", "targeted", "research", "main"]
ChallengeStatus = Literal["unknown", "active", "solved", "blocked", "expired"]
MemoryKind = Literal["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]
RiskLevel = Literal["passive", "active", "destructive"]
StrategyStatus = Literal["pending", "testing", "succeeded", "failed", "blocked"]
ExtractionStatus = Literal["not_requested", "blocked_out_of_scope", "failed", "extracted"]


class SessionRecord(BaseModel):
    task_id: str
    schema_version: int = 2
    status: SessionStatus = "created"
    active_solver_id: str | None = None
    turn_count: int = 0
    max_turns: int = 48
    started_at: str | None = None
    finished_at: str | None = None
    stop_reason: str = ""
    workspace_path: str = ""
    mcp_catalog_version: str = ""


class SolverRecord(BaseModel):
    id: str
    task_id: str
    role: SolverRole = "main"
    status: SolverStatus = "starting"
    model_name: str = ""
    parent_solver_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class MemoryEntry(BaseModel):
    id: str
    task_id: str
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)
    source: str
    supersedes_id: str | None = None
    created_at: str
    updated_at: str


class StrategySource(BaseModel):
    """A provenance anchor for untrusted hint or article content."""

    model_config = {"extra": "forbid"}

    hint_id: str | None = None
    url: str | None = None
    artifact_id: str | None = None
    extraction_status: ExtractionStatus = "not_requested"
    source_refs: list[str] = Field(default_factory=list, max_length=32)


class StrategyStep(BaseModel):
    """One candidate, evidence-producing test in a StrategyCard."""

    model_config = {"extra": "forbid"}

    id: str
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=1200)
    expected_request: str = Field(default="", max_length=800)
    success_marker: str = Field(default="", max_length=300)
    failure_conditions: list[str] = Field(default_factory=list, max_length=8)
    next_step_id: str | None = None
    risk: RiskLevel = "passive"
    status: StrategyStatus = "pending"
    action_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=128)
    last_result: str = Field(default="", max_length=800)


class StrategyCard(BaseModel):
    """Durable candidate strategy; source claims are never facts by default."""

    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    schema_version: int = 1
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=2000)
    claims: list[str] = Field(default_factory=list, max_length=24)
    prerequisites: list[str] = Field(default_factory=list, max_length=16)
    target_version_checks: list[str] = Field(default_factory=list, max_length=12)
    sources: list[StrategySource] = Field(default_factory=list, max_length=16)
    steps: list[StrategyStep] = Field(default_factory=list, max_length=32)
    status: StrategyStatus = "pending"
    active_step_id: str | None = None
    created_at: str
    updated_at: str


class ContextMetric(BaseModel):
    task_id: str
    solver_id: str
    turn: int = Field(ge=0)
    audit_message_count: int = Field(ge=0)
    working_message_count: int = Field(ge=0)
    working_chars: int = Field(ge=0)
    summary_hits: int = Field(default=0, ge=0)
    artifact_retrievals: int = Field(default=0, ge=0)
    retrieval_runs: int = Field(default=0, ge=0)
    retrieved_context_tokens: int = Field(default=0, ge=0)
    retrieval_failures: int = Field(default=0, ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    created_at: str


class ChallengeContract(BaseModel):
    """Durable completion state for an authorized challenge."""

    task_id: str
    entry_url: str | None = None
    allowed_origins: list[str]
    status: ChallengeStatus = "unknown"
    flag_format: str | None = None
    completion_proof_artifact_id: str | None = None
    status_reason: str = ""
    solved_at: str | None = None


__all__ = [
    "ChallengeContract", "ChallengeStatus", "ContextMetric", "ExtractionStatus",
    "MemoryEntry", "MemoryKind", "RiskLevel", "SessionRecord", "SessionStatus",
    "SolverRecord", "SolverRole", "SolverStatus", "StrategyCard", "StrategySource",
    "StrategyStatus", "StrategyStep",
]
