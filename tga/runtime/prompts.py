"""System prompt assembly for persistent Agent sessions."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.runtime.prompt_settings import prompt_snapshot_for_task


def build_agent_system_prompt(task: TGATask) -> str:
    settings = prompt_snapshot_for_task(task)
    return (
        f"{settings.common_system_prompt} {settings.mode.prompt()} "
        f"Mode configuration: {task.mode_config.model_dump_json() if task.mode_config else '{}'} "
        f"Execution policy: {task.execution_policy.model_dump_json() if task.execution_policy else '{}'}"
    )
