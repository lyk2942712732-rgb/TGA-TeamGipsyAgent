"""Historical schema-v5 Skill bundle conversions for offline migration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.modes import TaskMode
from tga.domain.skills.models import SkillSnapshot, SolverSkillSnapshot, TaskCommonSkillSnapshot


class LegacySkillSnapshot(BaseModel):
    """One Skill as it was persisted inside a schema-v5 task payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    version: str = Field(min_length=1, max_length=32)
    origin: Literal["builtin", "custom"]
    modes: list[TaskMode] = Field(min_length=1, max_length=5)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=32)
    body: str = Field(min_length=1, max_length=12_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    score: int = Field(ge=0, le=10_000)
    selection_reasons: list[str] = Field(min_length=1, max_length=12)


class LegacySkillBundleSnapshot(BaseModel):
    """The schema-v5 `task.skill_bundle_snapshot` payload, read-only."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    selector: str = Field(min_length=1, max_length=128)
    query_summary: str = Field(default="", max_length=2_000)
    skills: list[LegacySkillSnapshot] = Field(default_factory=list, max_length=3)
    total_chars: int = Field(default=0, ge=0, le=24_000)


def legacy_skill_bundle_to_task_common(
    bundle: LegacySkillBundleSnapshot,
    *,
    task_id: str,
    created_at: str,
) -> TaskCommonSkillSnapshot:
    skills = tuple(
        SkillSnapshot(
            name=skill.name,
            version=skill.version,
            modes=tuple(skill.modes),
            required_capabilities=tuple(skill.capabilities),
            tags=tuple(skill.tags),
            body=skill.body,
            content_sha256=skill.content_sha256,
            origin=skill.origin,
            selection_reasons=tuple(skill.selection_reasons),
        )
        for skill in bundle.skills
    )
    return TaskCommonSkillSnapshot(
        task_id=task_id,
        selector=f"legacy:{bundle.selector}",
        skills=skills,
        total_chars=bundle.total_chars,
        created_at=created_at,
        legacy_import=True,
    )


def legacy_skill_bundle_to_solver(
    bundle: LegacySkillBundleSnapshot,
    *,
    task_id: str,
    solver_id: str,
    solver_definition_id: str,
    created_at: str,
) -> SolverSkillSnapshot:
    common = legacy_skill_bundle_to_task_common(
        bundle, task_id=task_id, created_at=created_at
    )
    return SolverSkillSnapshot(
        task_id=task_id,
        solver_id=solver_id,
        solver_definition_id=solver_definition_id,
        selector=f"legacy-solver:{bundle.selector}",
        skills=common.skills,
        total_chars=common.total_chars,
        created_at=created_at,
        legacy_import=True,
    )


__all__ = [
    "LegacySkillBundleSnapshot", "LegacySkillSnapshot",
    "legacy_skill_bundle_to_solver", "legacy_skill_bundle_to_task_common",
]
