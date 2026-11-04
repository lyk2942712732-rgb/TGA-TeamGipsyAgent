"""Stable domain contracts shared by control-plane and worker processes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class EntryKind(StrEnum):
    USER_PROMPT = "user_prompt"
    USER_FILE = "user_file"
    INTEL = "intel"
    FINDING = "finding"
    FINAL_CANDIDATE = "final_candidate"


class RunState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    SCOUTING = "scouting"
    FINALIZING = "finalizing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentState(StrEnum):
    CREATED = "created"
    IDLE = "idle"
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
    role: Literal["user", "scout", "worker", "reporter", "system"]
    sdk: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)


USER_ACTOR = Actor(agent_id="user", display_name="User", role="user")
SYSTEM_ACTOR = Actor(agent_id="system", display_name="TGA3", role="system")


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: UUID = Field(description="Server-assigned UUID of a registered evidence file in this task.")
    locator: str = Field(
        min_length=1,
        max_length=1000,
        description="Precise evidence location inside the artifact, such as stdout:1-20 or solve.txt:3.",
    )
    description: str = Field(default="", max_length=2000, description="Brief explanation of this evidence.")


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




class FindingBody(BaseModel):
    """Compact, already-gated shared knowledge. Artifact details stay hidden."""

    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1, description="Concise verified fact; do not use title, summary, or flag fields.")
    detail: str = Field(default="", max_length=8000, description="Reproducible supporting detail for the claim.")




class FinalCandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: str = Field(min_length=1, description="The proposed final answer, for example the exact flag.")
    rationale: str = Field(min_length=1, description="Why the referenced verified Findings support the conclusion.")
    answer_type: str = Field(default="result", min_length=1, max_length=80, description="Answer category, e.g. flag.")
    finding_ids: list[UUID] = Field(
        min_length=1,
        description="Existing blackboard entry UUIDs whose kind is finding; never use Artifact UUIDs here.",
    )


class WorkerActor(Actor):
    """Actor identity accepted by the Worker-only MCP publication tool."""

    role: Literal["worker"] = "worker"


class WorkerPublicationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID = Field(description="Exact task UUID injected into the Worker system prompt.")
    actor: WorkerActor = Field(description="Exact Worker actor object injected into the system prompt.")
    topic: str = Field(default="general", min_length=1, max_length=200)
    idempotency_key: str = Field(
        min_length=1,
        max_length=200,
        description="Stable unique key for this logical publication; reuse it only when retrying the same entry.",
    )

    def publish_request(self, *, artifact_refs: list[ArtifactRef] | None = None) -> PublishRequest:
        payload = self.model_dump(mode="json")
        return PublishRequest(
            kind=payload["kind"],
            topic=payload["topic"],
            body=payload["body"],
            artifact_refs=artifact_refs if artifact_refs is not None else payload.get("artifact_refs", []),
            idempotency_key=payload["idempotency_key"],
            source_action_ids=payload.get("source_action_ids", []),
        )


class ScoutActor(Actor):
    role: Literal["scout"] = "scout"


class WorkerFinalCandidatePublication(WorkerPublicationBase):
    kind: Literal[EntryKind.FINAL_CANDIDATE]
    body: FinalCandidateBody


AgentPublishRequest: TypeAlias = WorkerFinalCandidatePublication


def publication_contract(role: str | None = None) -> str:
    """Generate the compact prompt/tool description from the authoritative Pydantic models."""

    if role == "scout":
        return ("scout_write_intel(action_id, claim, detail?, topic?, evidence_paths?). "
                "scout_merge_intel(merged_from_ids, claim?, detail?, topic?). "
                "Runtime binds identity; a singleton is copied verbatim by the server.")
    fields = ", ".join(f"{name}{'' if field.is_required() else '?'}"
                       for name, field in FinalCandidateBody.model_fields.items())
    return ("finding_publish: action_id, claim, scope, preconditions?. "
            f"blackboard_publish: {fields}, topic?. "
            "Task, actor, kind and retry key are supplied by the runtime; no request wrapper.")


BODY_BY_KIND: dict[EntryKind, type[BaseModel]] = {
    EntryKind.USER_PROMPT: UserPromptBody,
    EntryKind.USER_FILE: UserFileBody,
    EntryKind.INTEL: FindingBody,
    EntryKind.FINDING: FindingBody,
    EntryKind.FINAL_CANDIDATE: FinalCandidateBody,
}


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EntryKind
    topic: str | None = Field(default="general", min_length=1, max_length=200)
    body: dict[str, Any]
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=200)
    source_action_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_contract(self) -> PublishRequest:
        BODY_BY_KIND[self.kind].model_validate(self.body)
        if self.kind == EntryKind.FINDING:
            self.topic = None
        if self.kind == EntryKind.FINDING and not self.artifact_refs:
            raise ValueError("finding requires at least one artifact reference")
        if self.kind not in {EntryKind.INTEL, EntryKind.FINDING} and self.artifact_refs:
            raise ValueError("artifact references are accepted only for intel and finding")
        return self

    def validated_body(self) -> BaseModel:
        return BODY_BY_KIND[self.kind].model_validate(self.body)


class QuestionScoped(BaseModel):
    """Question provenance is derived by the runtime, absent on ordinary tasks."""

    question_id: UUID | None = None

    @model_serializer(mode="wrap")
    def serialize_scope(self, handler):
        value = handler(self)
        if self.question_id is None:
            value.pop("question_id", None)
        return value


class BlackboardEntry(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    seq: int = Field(ge=1)
    actor: Actor
    kind: EntryKind
    topic: str | None = None
    body: dict[str, Any]
    idempotency_key: str
    source_action_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_serializer(mode="wrap")
    def serialize_entry(self, handler):
        value = handler(self)
        if self.kind == EntryKind.FINDING:
            value.pop("topic", None)
        if self.question_id is None:
            value.pop("question_id", None)
        return value


class BlackboardSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    published: BlackboardEntry | None = None
    latest_seq: int = Field(ge=0)
    entries: tuple[BlackboardEntry, ...] = ()


class Artifact(QuestionScoped):
    """Registered evidence file index; an action's artifact bundles its output and attachments."""

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


class InputFile(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    name: str = Field(min_length=1, max_length=500)
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class Writeup(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    snapshot_seq: int = Field(ge=1)
    storage_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=utc_now)


class EventKind(StrEnum):
    ASSISTANT_DELTA = "assistant_delta"
    BLACKBOARD_PROGRESS = "blackboard_progress"
    AGENT_STATUS = "agent_status"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    MODEL_CHANGED = "model_changed"
    PAUSED = "paused"
    RESUMED = "resumed"
    ERROR = "error"
    SYSTEM = "system"


class TaskEvent(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    seq: int = Field(ge=1)
    channel_agent_id: str = "system"
    actor: Actor
    kind: EventKind
    text: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)




class TaskRun(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=500)
    scene_id: str = Field(min_length=1, max_length=100)
    state: RunState = RunState.CREATED
    blackboard_seq: int = 0
    event_seq: int = 0
    final_snapshot_seq: int | None = None
    parent_task_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_serializer(mode="wrap")
    def serialize_task(self, handler):
        value = handler(self)
        if self.parent_task_id is None:
            value.pop("parent_task_id", None)
            value.pop("question_id", None)
        if not self.metadata:
            value.pop("metadata", None)
        return value


class AgentRun(QuestionScoped):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: UUID
    agent_id: str
    sdk: Literal["codex_cli", "claude_cli"]
    desired_state: AgentState = AgentState.CREATED
    actual_state: AgentState = AgentState.CREATED
    provider_id: str
    model_id: str
    protocol: Literal["openai_responses", "openai_chat_completions", "anthropic"]
    container_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
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
    "EventKind",
    "TaskEvent",
    "EntryKind",
    "FinalCandidateBody",
    "FindingBody",
    "InputFile",
    "PublishRequest",
    "RunState",
    "SYSTEM_ACTOR",
    "TaskRun",
    "USER_ACTOR",
    "UserFileBody",
    "UserPromptBody",
    "WorkerActor",
    "WorkerFinalCandidatePublication",
    "AgentPublishRequest",
    "Writeup",
    "utc_now",
    "publication_contract",
]
