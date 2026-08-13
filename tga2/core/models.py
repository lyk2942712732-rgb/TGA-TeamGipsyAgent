"""Compact product model: tasks, plans, evidence and audit events."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_USER_INPUT = "awaiting_user_input"
    COMPLETED = "completed"
    COMPLETED_WITH_LIMITATIONS = "completed_with_limitations"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=4096)
    media_type: str = Field(default="application/octet-stream", max_length=255)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    role: Literal["target", "reference"] = "target"


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    objective: str = Field(min_length=1, max_length=8000)
    instructions: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    success_criteria: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    resources: tuple[ResourceRef, ...] = Field(default_factory=tuple, max_length=256)
    mode_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_task_model_assignments(cls, value: Any) -> Any:
        if isinstance(value, dict) and "agent_models" in value:
            value = dict(value)
            value.pop("agent_models", None)
        return value

    @model_validator(mode="after")
    def validate_text(self) -> TaskSpec:
        for group in (self.instructions, self.constraints, self.success_criteria):
            if any(not item.strip() for item in group):
                raise ValueError("task directives cannot be blank")
        return self


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=255)
    mode: str = Field(default="ctf", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    spec: TaskSpec
    status: TaskStatus = TaskStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IntentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=4000)
    assigned_solver_id: str = "worker"
    dependencies: tuple[str, ...] = ()
    priority: int = Field(default=50, ge=0, le=100)
    status: IntentStatus = IntentStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    version: int = Field(default=1, ge=1)
    summary: str
    intents: tuple[Intent, ...]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SolverRole(StrEnum):
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    REVIEWER = "reviewer"
    REPORTER = "reporter"


class SolverRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    solver_id: str
    role: SolverRole
    intent_id: str | None = None
    status: str = "created"
    summary: str = ""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EvidenceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["whole", "line_range", "text_range", "json_path", "page"] = "whole"
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    json_path: str | None = None
    page: int | None = Field(default=None, ge=1)
    quote: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def validate_coordinates(self) -> EvidenceLocator:
        if self.kind == "line_range" and (
            self.line_start is None
            or self.line_end is None
            or self.line_end < self.line_start
        ):
            raise ValueError("line_range requires an ordered line_start and line_end")
        if self.kind == "text_range" and (
            self.char_start is None
            or self.char_end is None
            or self.char_end <= self.char_start
        ):
            raise ValueError("text_range requires char_end greater than char_start")
        if self.kind == "json_path" and not self.json_path:
            raise ValueError("json_path locator requires json_path")
        if self.kind == "page" and self.page is None:
            raise ValueError("page locator requires page")
        return self


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    kind: str = Field(min_length=1, max_length=128)
    path: str
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    media_type: str = "text/plain"
    tool_name: str | None = None
    intent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    artifact_id: str
    statement: str = Field(min_length=1, max_length=8000)
    locator: EvidenceLocator
    status: Literal["candidate", "confirmed", "rejected"] = "candidate"
    created_by: str = "worker"
    reviewed_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=8000)
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    status: Literal["candidate", "confirmed", "rejected"] = "candidate"
    evidence_claim_ids: tuple[str, ...] = ()
    remediation: str | None = Field(default=None, max_length=8000)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def confirmed_needs_evidence(self) -> Finding:
        if self.status == "confirmed" and not self.evidence_claim_ids:
            raise ValueError("confirmed finding requires evidence claims")
        return self


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(default=0, ge=0)
    task_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    solver_id: str | None = None
    intent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=8000)
    mode: str = Field(default="ctf", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    mode_options: dict[str, Any] = Field(default_factory=dict)
    instructions: list[str] = Field(default_factory=list, max_length=128)
    constraints: list[str] = Field(default_factory=list, max_length=128)
    success_criteria: list[str] = Field(default_factory=list, max_length=128)
    input_paths: list[str] = Field(default_factory=list, max_length=256)
    execution_policy: Any = None  # ExecutionPolicy; Any avoids a core module cycle.


__all__ = [
    "AgentEvent",
    "Artifact",
    "CreateTaskRequest",
    "EvidenceClaim",
    "EvidenceLocator",
    "Finding",
    "Intent",
    "IntentStatus",
    "Plan",
    "ResourceRef",
    "SolverRole",
    "SolverRun",
    "Task",
    "TaskSpec",
    "TaskStatus",
    "utc_now",
]
