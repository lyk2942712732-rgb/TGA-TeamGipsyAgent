"""Skill documents and immutable selection snapshots."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.modes import TaskMode


class SkillDocument(BaseModel):
    """Validated source guidance before selection and freezing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    body: str = Field(min_length=1, max_length=500_000)
    origin: Literal["builtin", "custom", "resource"]
    source_ref: str


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    modes: tuple[TaskMode, ...] = Field(min_length=1, max_length=5)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    tags: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    body: str = Field(min_length=1, max_length=12_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: Literal["builtin", "custom", "resource", "legacy_import"]
    selection_reasons: tuple[str, ...] = Field(min_length=1, max_length=16)

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


def _validate_skill_bundle(skills: tuple[SkillSnapshot, ...], total_chars: int) -> None:
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise ValueError("Skill snapshot names must be unique")
    if total_chars != sum(len(skill.body) for skill in skills):
        raise ValueError("Skill snapshot total_chars does not match frozen bodies")


__all__ = [
    "SkillActivation", "SkillDocument", "SkillSnapshot", "SolverSkillSnapshot",
    "TaskCommonSkillSnapshot",
]
