"""Shared cross-module contracts for TGA Week 1.

All teams should import these models instead of redefining task, intent,
artifact, finding, or worker-result shapes locally.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

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
SessionStatus = Literal["created", "running", "paused", "blocked", "completed", "failed", "cancelled"]
SolverStatus = Literal["starting", "running", "waiting", "completed", "failed", "cancelled"]
SolverRole = Literal["recon", "targeted", "research", "main"]
ChallengeStatus = Literal["unknown", "active", "solved", "blocked", "expired"]
HypothesisStatus = Literal["pending", "testing", "verified", "rejected", "inconclusive", "superseded"]
MemoryKind = Literal["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]
ActionKind = Literal["http", "tool", "workspace", "browser"]
ActionStatus = Literal["proposed", "approved", "running", "succeeded", "failed", "blocked", "cancelled"]


class TGAError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class TGATask(BaseModel):
    id: str
    name: str
    mode: TaskMode
    target: str
    # Kept only so existing task files remain readable. Product sessions use
    # ``target`` as the challenge authorization contract and derive this
    # compatibility value automatically.
    scope: list[str] = Field(default_factory=list)
    target_theme: str = ""
    target_description: str = ""
    intensity: Intensity = "normal"
    allow_active_scan: bool = False
    goal: str
    flag_format: str | None = None
    # A CTF platform can occasionally use an incomplete/self-signed chain.
    # This is never a global TLS switch: every exception is an exact HTTPS
    # origin that must already be inside this task's authorization scope.
    insecure_tls_origins: list[str] = Field(default_factory=list, max_length=8)
    # Version 1 payloads omitted this field.  The default keeps them readable;
    # the runtime only creates a v2 session after an explicit start request.
    schema_version: int = 2

    @model_validator(mode="after")
    def validate_authorized_scope(self) -> "TGATask":
        self.target = self.target.strip()
        self.scope = list(dict.fromkeys(item.strip() for item in self.scope if item.strip()))
        if not self.scope and self.target:
            parsed_target = urlparse(self.target)
            self.scope = [
                f"{parsed_target.scheme}://{parsed_target.netloc}"
                if parsed_target.scheme and parsed_target.netloc
                else self.target
            ]
        if self.flag_format:
            if len(self.flag_format) > 256:
                raise ValueError("flag_format exceeds 256 characters")
            try:
                re.compile(self.flag_format)
            except re.error as exc:
                raise ValueError(f"invalid flag_format: {exc}") from exc
        target_origin = _https_origin(self.target)
        from tga.core.scope import is_in_scope

        canonical_origins: list[str] = []
        for value in self.insecure_tls_origins:
            origin = _https_origin(value)
            if origin is None or origin != target_origin:
                raise ValueError("insecure_tls_origins may contain only the exact HTTPS target origin")
            if not is_in_scope(origin, self.scope):
                raise ValueError("insecure_tls_origins must be inside task scope")
            if origin not in canonical_origins:
                canonical_origins.append(origin)
        self.insecure_tls_origins = canonical_origins
        return self


def _https_origin(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    return f"https://{host}" if port in {None, 443} else f"https://{host}:{port}"


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


class SolverRecord(BaseModel):
    id: str
    task_id: str
    role: SolverRole = "main"
    status: SolverStatus = "starting"
    model_name: str = ""
    parent_solver_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class Hypothesis(BaseModel):
    id: str
    task_id: str
    statement: str
    attack_class: str
    entry_point: str
    rationale: str
    next_test: str
    status: HypothesisStatus = "pending"
    confidence: float = Field(ge=0, le=1)
    attempt_count: int = 0
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    last_result: str = ""
    owner_solver_id: str | None = None
    created_at: str
    updated_at: str


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


class ActionSpec(BaseModel):
    """The sole request shape accepted by a controlled executor (A -> B)."""

    id: str
    task_id: str
    solver_id: str
    hypothesis_id: str | None = None
    kind: ActionKind
    capability: str
    target: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    risk: RiskLevel


class ActionResult(BaseModel):
    """The sole execution result shape returned to the orchestration runtime."""

    action_id: str
    task_id: str
    solver_id: str
    status: ActionStatus
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    candidate_flags: list[str] = Field(default_factory=list)
    candidate_findings: list[Finding] = Field(default_factory=list)
    error: TGAError | None = None


class AgentEvent(BaseModel):
    schema_version: int = 2
    id: str
    task_id: str
    solver_id: str | None = None
    seq: int = Field(ge=1)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ChallengeContract(BaseModel):
    """Durable completion state for an authorized challenge.

    TGA deliberately has no submission fields: a provenance-backed
    ``FLAG_CONFIRMED`` event is the sole solved oracle.
    """

    task_id: str
    entry_url: str
    allowed_origins: list[str]
    status: ChallengeStatus = "unknown"
    flag_format: str | None = None
    completion_proof_artifact_id: str | None = None
    status_reason: str = ""
    solved_at: str | None = None


class HypothesisDraft(BaseModel):
    model_config = {"extra": "forbid"}

    statement: str = Field(min_length=1)
    attack_class: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    next_test: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class HypothesisUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    hypothesis_id: str
    status: HypothesisStatus
    last_result: str = Field(default="", max_length=800)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    decisive: bool = False


class FactDraft(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)


class FailureBoundaryDraft(BaseModel):
    model_config = {"extra": "forbid"}

    attack_class: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)


class SubagentRequest(BaseModel):
    """The only context hand-off accepted when Manager starts a child Solver."""

    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    parent_solver_id: str
    role: SolverRole
    objective: str = Field(min_length=1, max_length=800)
    hypothesis_ids: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    max_actions: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def child_role_only(self) -> "SubagentRequest":
        if self.role == "main":
            raise ValueError("main is the manager-owned coordinator, not a subagent role")
        return self


class SubagentOutput(BaseModel):
    """Bounded, schema-validated child Solver hand-off."""

    model_config = {"extra": "forbid"}

    request_id: str
    solver_id: str
    status: Literal["completed", "blocked", "failed"]
    hypotheses: list[HypothesisDraft] = Field(default_factory=list, max_length=5)
    result_updates: list[HypothesisUpdate] = Field(default_factory=list, max_length=8)
    facts: list[FactDraft] = Field(default_factory=list, max_length=8)
    failure_boundaries: list[FailureBoundaryDraft] = Field(default_factory=list, max_length=8)
    candidate_flags: list[str] = Field(default_factory=list, max_length=8)
    artifact_ids: list[str] = Field(default_factory=list, max_length=32)
    coverage_gaps: list[str] = Field(default_factory=list, max_length=8)
    next_recommendation: str = Field(default="", max_length=800)


# Compatibility aliases for early consumers of the advanced contract draft.
HypothesisDraftContract = HypothesisDraft
HypothesisUpdateContract = HypothesisUpdate

