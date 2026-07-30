"""Canonical locations for legacy evidence-facing schema-v5 models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


IntentKind = Literal["recon", "verify", "exploit_ctf", "code_scan", "report"]
IntentStatus = Literal["pending", "running", "done", "failed", "blocked"]
FindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]
ArtifactKind = Literal["stdout", "stderr", "tool_output", "http_response", "http_body", "file", "report"]
WorkerStatus = Literal["ok", "failed", "blocked"]
RiskLevel = Literal["passive", "active", "destructive"]
DecisionPhase = Literal["planning", "execution", "adaptation", "gate"]
ExtractionStatus = Literal["not_requested", "blocked_out_of_scope", "failed", "extracted"]


class Intent(BaseModel):
    id: str
    task_id: str
    kind: IntentKind
    target: str
    goal: str
    required_tools: list[str] = Field(default_factory=list)
    risk: RiskLevel = "passive"
    status: IntentStatus = "pending"


class ArtifactRecord(BaseModel):
    id: str
    task_id: str
    intent_id: str | None = None
    kind: ArtifactKind
    path: str
    sha256: str
    tool: str | None = None
    target: str | None = None
    input_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class Finding(BaseModel):
    id: str
    task_id: str
    title: str
    target: str
    severity: Severity
    status: FindingStatus = "candidate"
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    remediation: str | None = None
    tool: str | None = None


class WorkerResult(BaseModel):
    task_id: str
    intent_id: str
    status: WorkerStatus
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DecisionTrace(BaseModel):
    task_id: str
    phase: DecisionPhase
    summary: str
    rationale: str
    intent_id: str | None = None
    inputs: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    next_action: str | None = None


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


class AgentEvent(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: int = 2
    id: str
    task_id: str
    solver_id: str | None = None
    intent_id: str | None = None
    seq: int = Field(ge=1)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


__all__ = [
    "AgentEvent", "ArtifactIndex", "ArtifactKind", "ArtifactRecord",
    "ArtifactSegment", "DecisionPhase", "DecisionTrace", "ExtractionStatus",
    "Finding", "FindingStatus", "Intent", "IntentKind", "IntentStatus",
    "RiskLevel", "Severity", "WorkerResult", "WorkerStatus",
]
