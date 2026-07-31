"""Current schema-v6 evidence records used by execution and persistence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ArtifactKind = Literal[
    "stdout", "stderr", "tool_output", "http_response", "http_body", "file", "report",
]
CandidateFindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]


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


class CandidateFindingRecord(BaseModel):
    """Execution candidate awaiting conversion into reviewed schema-v6 evidence."""

    id: str
    task_id: str
    title: str
    target: str
    severity: Severity
    status: CandidateFindingStatus = "candidate"
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    remediation: str | None = None
    tool: str | None = None


__all__ = [
    "ArtifactKind", "ArtifactRecord", "CandidateFindingRecord",
    "CandidateFindingStatus", "Severity",
]
