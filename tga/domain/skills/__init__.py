"""Immutable skill guidance snapshots; skills never grant tools."""

from tga.domain.skills.models import (
    SkillActivation,
    SkillDocument,
    SkillSnapshot,
    SolverSkillSnapshot,
    TaskCommonSkillSnapshot,
)
from tga.domain.skills.compatibility import legacy_skill_bundle_to_solver, legacy_skill_bundle_to_task_common

__all__ = [
    "SkillActivation", "SkillDocument", "SkillSnapshot", "SolverSkillSnapshot",
    "TaskCommonSkillSnapshot", "legacy_skill_bundle_to_solver", "legacy_skill_bundle_to_task_common",
]
