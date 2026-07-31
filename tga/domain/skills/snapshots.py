"""Freeze current selector output into schema-v6 Skill snapshots."""

from __future__ import annotations

from tga.domain.skills.models import SkillSnapshot, TaskCommonSkillSnapshot
from tga.skills.models import SkillBundleSnapshot


def current_skill_bundle_to_task_common(
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
        for skill in bundle.skills[:2]
    )
    return TaskCommonSkillSnapshot(
        task_id=task_id,
        selector=f"task-common:{bundle.selector}",
        skills=skills,
        total_chars=sum(len(skill.body) for skill in skills),
        created_at=created_at,
        legacy_import=False,
    )


__all__ = ["current_skill_bundle_to_task_common"]
