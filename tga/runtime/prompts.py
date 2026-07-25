"""System prompt assembly for persistent Agent sessions."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.runtime.prompt_settings import prompt_snapshot_for_task
from tga.skills.context import SkillContextAssembler


def build_agent_system_prompt(task: TGATask) -> str:
    settings = prompt_snapshot_for_task(task)
    base = (
        f"{settings.common_system_prompt} {settings.mode.prompt()} "
        f"Mode configuration: {task.mode_config.model_dump_json() if task.mode_config else '{}'} "
        f"Execution policy: {task.execution_policy.model_dump_json() if task.execution_policy else '{}'}"
    )
    skill_block = SkillContextAssembler().system_block(task.skill_bundle_snapshot)
    return f"{base}\n\n{skill_block}" if skill_block else base
