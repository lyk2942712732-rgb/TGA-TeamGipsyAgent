"""Skill documents and immutable selection snapshots."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.modes import TaskMode


class SkillDocument(BaseModel):
    """Validated source guidance before selection and freezing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    capability_requirements: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    body: str = Field(min_length=1, max_length=500_000)
    origin: Literal["builtin", "custom", "resource", "retrieval"]
    source_ref: str


SkillPublicationStatus = Literal[
    "draft", "reviewed", "published", "deprecated", "revoked"
]


class SkillPublication(BaseModel):
    """Append-only publication policy for one immutable Skill revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    revision_id: str
    document_id: str
    skill_name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    skill_version: str = Field(min_length=1, max_length=32)
    status: SkillPublicationStatus
    requires: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    conflicts_with: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    supersedes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    compatible_solver_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    compatible_intent_kinds: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    published_by: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=2_000)
    created_at: str


class SkillCandidate(BaseModel):
    """A retrieval hit carrying references, never executable Skill text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    retrieval_run_id: str
    index_snapshot_id: str
    knowledge_base_id: str
    source_id: str
    document_id: str
    revision_id: str
    owner: dict[str, Any]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    capability_requirements: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieval_score: float = Field(ge=0)
    trust_level: str
    publication_status: SkillPublicationStatus
    safety_flags: tuple[str, ...] = ()


class SkillCandidateRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    reason: str = Field(min_length=1, max_length=2_000)


class SkillSelectionDecision(BaseModel):
    """Replayable decision produced before a Solver Skill snapshot is frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    solver_id: str
    intent_id: str | None = None
    solver_definition_id: str
    retrieval_run_ids: tuple[str, ...] = ()
    index_snapshot_ids: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    selected_candidate_ids: tuple[str, ...] = ()
    selected_skill_names: tuple[str, ...] = ()
    rejected_candidates: tuple[SkillCandidateRejection, ...] = ()
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    capability_requirements: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    body: str = Field(min_length=1, max_length=12_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: Literal["builtin", "custom", "resource", "retrieval", "legacy_import"]
    selection_reasons: tuple[str, ...] = Field(min_length=1, max_length=16)
    source_ref: str | None = None
    document_id: str | None = None
    revision_id: str | None = None
    retrieval_run_id: str | None = None
    index_snapshot_id: str | None = None

    @model_validator(mode="after")
    def validate_content_hash(self) -> "SkillSnapshot":
        actual = hashlib.sha256(self.body.encode("utf-8")).hexdigest()
        if actual != self.content_sha256:
            raise ValueError("SkillSnapshot content_sha256 does not match frozen body")
        return self


class TaskCommonSkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    task_id: str
    selector: str = Field(min_length=1, max_length=128)
    # New selections are bounded to 2 by the service. Legacy schema-v5 bundles
    # may contain 3 and remain losslessly readable when legacy_import=True.
    skills: tuple[SkillSnapshot, ...] = Field(default_factory=tuple, max_length=3)
    total_chars: int = Field(ge=0, le=24_000)
    created_at: str
    legacy_import: bool = False

    @model_validator(mode="after")
    def validate_bundle(self) -> "TaskCommonSkillSnapshot":
        if not self.legacy_import and len(self.skills) > 2:
            raise ValueError("new Task Common Skill snapshots support at most 2 Skills")
        _validate_skill_bundle(self.skills, self.total_chars)
        return self

    @property
    def fingerprint(self) -> str:
        return ":".join(item.content_sha256[:12] for item in self.skills) or "empty"


class SolverSkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    task_id: str
    solver_id: str
    solver_definition_id: str
    intent_id: str | None = None
    selector: str = Field(min_length=1, max_length=128)
    skills: tuple[SkillSnapshot, ...] = Field(default_factory=tuple, max_length=3)
    total_chars: int = Field(ge=0, le=36_000)
    created_at: str
    legacy_import: bool = False
    skill_index_snapshot_ids: tuple[str, ...] = ()
    selection_decision_id: str | None = None

    @model_validator(mode="after")
    def validate_bundle(self) -> "SolverSkillSnapshot":
        _validate_skill_bundle(self.skills, self.total_chars)
        return self


class SkillActivation(BaseModel):
    """Auditable activation of guidance, with no tool-granting fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    solver_id: str
    skill_name: str
    skill_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["task_common", "solver_specialized"]
    reason: str = Field(min_length=1, max_length=2_000)
    activated_at: str
    document_id: str | None = None
    revision_id: str | None = None
    retrieval_run_id: str | None = None
    index_snapshot_id: str | None = None
    selection_decision_id: str | None = None


def _validate_skill_bundle(skills: tuple[SkillSnapshot, ...], total_chars: int) -> None:
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise ValueError("Skill snapshot names must be unique")
    if total_chars != sum(len(skill.body) for skill in skills):
        raise ValueError("Skill snapshot total_chars does not match frozen bodies")


__all__ = [
    "SkillActivation", "SkillCandidate", "SkillCandidateRejection", "SkillDocument",
    "SkillPublication", "SkillPublicationStatus", "SkillSelectionDecision",
    "SkillSnapshot", "SolverSkillSnapshot", "TaskCommonSkillSnapshot",
]
