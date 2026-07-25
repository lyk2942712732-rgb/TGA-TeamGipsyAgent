"""Runtime contracts for selected, immutable Skill guidance."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from tga.modes import TaskMode


SkillOrigin = Literal["builtin", "custom"]


class SkillSnapshot(BaseModel):
    """One Skill frozen for a task before the model session starts."""

    model_config = {"extra": "forbid"}

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    origin: SkillOrigin
    modes: list[TaskMode] = Field(min_length=1, max_length=5)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=32)
    body: str = Field(min_length=1, max_length=12_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    score: int = Field(ge=0, le=10_000)
    selection_reasons: list[str] = Field(min_length=1, max_length=12)


class SkillBundleSnapshot(BaseModel):
    """Auditable Skill selection result persisted with a task."""

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    selector: str = Field(min_length=1, max_length=128)
    query_summary: str = Field(default="", max_length=2_000)
    skills: Annotated[list[SkillSnapshot], Field(max_length=3)] = Field(default_factory=list)
    total_chars: int = Field(default=0, ge=0, le=24_000)

    @model_validator(mode="after")
    def validate_bundle(self) -> "SkillBundleSnapshot":
        names = [item.name for item in self.skills]
        if len(names) != len(set(names)):
            raise ValueError("skill snapshot names must be unique")
        actual_chars = sum(len(item.body) for item in self.skills)
        if self.total_chars != actual_chars:
            raise ValueError("skill snapshot total_chars does not match selected bodies")
        return self

    @property
    def fingerprint(self) -> str:
        return ":".join(item.content_sha256[:12] for item in self.skills) or "empty"
