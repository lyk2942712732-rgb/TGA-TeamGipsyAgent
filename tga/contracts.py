"""Shared cross-module contracts for TGA Week 1.

All teams should import these models instead of redefining task, intent,
artifact, finding, or worker-result shapes locally.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


TaskMode = Literal["ctf", "web_audit", "code_audit", "binary_ctf"]
Intensity = Literal["passive", "normal", "active"]
IntentKind = Literal["recon", "verify", "exploit_ctf", "code_scan", "report"]
IntentStatus = Literal["pending", "running", "done", "failed", "blocked"]
FindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]
ArtifactKind = Literal["stdout", "stderr", "tool_output", "http_response", "file", "report"]
WorkerStatus = Literal["ok", "failed", "blocked"]
RiskLevel = Literal["passive", "active", "destructive"]
DecisionPhase = Literal["planning", "execution", "adaptation", "gate"]


class TGAError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class TGATask(BaseModel):
    id: str
    name: str
    mode: TaskMode
    target: str
    scope: list[str]
    intensity: Intensity = "normal"
    allow_active_scan: bool = False
    goal: str
    flag_format: str | None = None

    @model_validator(mode="after")
    def validate_authorized_scope(self) -> "TGATask":
        if self.mode == "web_audit" and not [item for item in self.scope if item.strip()]:
            raise ValueError("web_audit requires non-empty scope")
        return self


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

