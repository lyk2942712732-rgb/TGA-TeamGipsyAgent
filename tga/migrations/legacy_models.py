"""Historical schema-v5 models used only by offline migration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SolverStatus = Literal["starting", "running", "waiting", "completed", "failed", "cancelled"]
SolverRole = Literal["recon", "targeted", "research", "main"]
MemoryKind = Literal["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]
RiskLevel = Literal["passive", "active", "destructive"]
StrategyStatus = Literal["pending", "testing", "succeeded", "failed", "blocked"]
ExtractionStatus = Literal["not_requested", "blocked_out_of_scope", "failed", "extracted"]


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


__all__ = [
    "ExtractionStatus", "MemoryEntry", "MemoryKind", "RiskLevel",
    "SolverRecord", "SolverRole", "SolverStatus", "StrategyCard", "StrategySource",
    "StrategyStatus", "StrategyStep",
]
