"""Historical schema-v5 Skill bundle conversions for offline migration."""

from __future__ import annotations

from tga.domain.skills.models import SkillSnapshot, SolverSkillSnapshot, TaskCommonSkillSnapshot
from tga.skills.models import SkillBundleSnapshot


def legacy_skill_bundle_to_task_common(
    bundle: SkillBundleSnapshot,
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
    bundle: SkillBundleSnapshot,
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


__all__ = ["legacy_skill_bundle_to_solver", "legacy_skill_bundle_to_task_common"]
