"""Stable domain contracts shared by control-plane and worker processes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class EntryKind(StrEnum):
    USER_PROMPT = "user_prompt"
    USER_FILE = "user_file"
    SUPERVISOR_ADVICE = "supervisor_advice"
    FINDING = "finding"
    QA = "qa"
    FINAL_CANDIDATE = "final_candidate"


class RunState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    FINALIZING = "finalizing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class Actor(BaseModel):
    """Self-declared provenance metadata, intentionally not authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["user", "supervisor", "worker", "reporter", "system"]
    sdk: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)


USER_ACTOR = Actor(agent_id="user", display_name="User", role="user")
SYSTEM_ACTOR = Actor(agent_id="system", display_name="TGA3", role="system")


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: UUID
    locator: str = Field(min_length=1, max_length=1000)
    description: str = Field(default="", max_length=2000)


class UserPromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list)
    addressed_to: list[str] = Field(default_factory=list)


class UserFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_file_id: UUID
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    description: str = Field(default="", max_length=2000)


class SupervisorAdviceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    advice: str = Field(min_length=1)
    addressed_to: list[str] = Field(default_factory=list)
    based_on_seq: int = Field(default=0, ge=0)


class FindingBody(BaseModel):
    """Compact, already-gated shared knowledge. Artifact details stay hidden."""

    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1)
    detail: str = Field(default="", max_length=8000)


class QABody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    origin_type: Literal["supervisor", "agent"]
    origin_agent_id: str
    answer_attachment_ids: list[UUID] = Field(default_factory=list)


class FinalCandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    answer_type: str = Field(default="result", min_length=1, max_length=80)
    finding_ids: list[UUID] = Field(min_length=1)


BODY_BY_KIND: dict[EntryKind, type[BaseModel]] = {
    EntryKind.USER_PROMPT: UserPromptBody,
    EntryKind.USER_FILE: UserFileBody,
    EntryKind.SUPERVISOR_ADVICE: SupervisorAdviceBody,
    EntryKind.FINDING: FindingBody,
    EntryKind.QA: QABody,
    EntryKind.FINAL_CANDIDATE: FinalCandidateBody,
}


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EntryKind
    topic: str = Field(default="general", min_length=1, max_length=200)
    body: dict[str, Any]
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_contract(self) -> PublishRequest:
        BODY_BY_KIND[self.kind].model_validate(self.body)
        if self.kind == EntryKind.FINDING and not self.artifact_refs:
            raise ValueError("finding requires at least one artifact reference")
        if self.kind != EntryKind.FINDING and self.artifact_refs:
            raise ValueError("artifact references are accepted only for finding")
        return self

    def validated_body(self) -> BaseModel:
        return BODY_BY_KIND[self.kind].model_validate(self.body)


class BlackboardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    seq: int = Field(ge=1)
    actor: Actor
    kind: EntryKind
    topic: str
    body: dict[str, Any]
    idempotency_key: str
    created_at: datetime = Field(default_factory=utc_now)


class BlackboardSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    published: BlackboardEntry | None = None
    latest_seq: int = Field(ge=0)
    entries: tuple[BlackboardEntry, ...] = ()


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    created_by: Actor
    name: str = Field(min_length=1, max_length=500)
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    available: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class InputFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    name: str = Field(min_length=1, max_length=500)
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class DialogueKind(StrEnum):
    ASSISTANT_DELTA = "assistant_delta"
    BLACKBOARD_PROGRESS = "blackboard_progress"
    AGENT_STATUS = "agent_status"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    QUESTION = "question"
    USER_MESSAGE = "user_message"
    MODEL_CHANGED = "model_changed"
    PAUSED = "paused"
    RESUMED = "resumed"
    ERROR = "error"
    SYSTEM = "system"


class DialogueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    seq: int = Field(ge=1)
    channel_agent_id: str = "supervisor"
    actor: Actor
    kind: DialogueKind
    text: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class PendingQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    question: str = Field(min_length=1)
    origin: Actor
    asked_by: Actor
    state: Literal["waiting", "answered"] = "waiting"
    created_at: datetime = Field(default_factory=utc_now)


class TaskRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=500)
    state: RunState = RunState.CREATED
    blackboard_seq: int = 0
    dialogue_seq: int = 0
    final_snapshot_seq: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: UUID
    agent_id: str
    sdk: Literal["openai_agents", "claude_agent"]
    desired_state: AgentState = AgentState.CREATED
    actual_state: AgentState = AgentState.CREATED
    provider_id: str
    model_id: str
    container_id: str | None = None
    session_id: str | None = None
    last_error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


__all__ = [
    "Actor",
    "AgentRun",
    "AgentState",
    "Artifact",
    "ArtifactRef",
    "BlackboardEntry",
    "BlackboardSyncResult",
    "DialogueKind",
    "DialogueMessage",
    "EntryKind",
    "FinalCandidateBody",
    "FindingBody",
    "InputFile",
    "PendingQuestion",
    "PublishRequest",
    "QABody",
    "RunState",
    "SYSTEM_ACTOR",
    "SupervisorAdviceBody",
    "TaskRun",
    "USER_ACTOR",
    "UserFileBody",
    "UserPromptBody",
    "utc_now",
]
