"""Load a CLI task request into the shared task-creation command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tga.contracts import ExecutionPolicy
from tga.runtime.task_creation import CreateTaskCommand
from tga.skills.selection import MAX_SELECTED_SKILLS


class TaskConfigError(ValueError):
    """Raised when a task config cannot be read or validated."""


ALLOWED_KEYS = frozenset({
    "id", "name", "mode", "goal", "modeOptions", "input", "executionPolicy",
    "selectedSkills", "workspaceId",
})


def load_task_request(path: str | Path) -> CreateTaskCommand:
    """Read task.json as the same creation form the Web/API surface uses.

    The CLI never constructs a persisted task directly.  It builds the same
    command that `TaskCreationService.preflight` and `.create` consume, so both
    surfaces share one validation, Skill selection and preflight path.
    """
    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskConfigError(f"task config not found: {config_path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskConfigError(f"invalid JSON in {config_path}: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise TaskConfigError(f"task config must be a JSON object: {config_path}")

    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise TaskConfigError(
            f"unsupported task config fields in {config_path}: {', '.join(unknown)}"
        )
    for key in ("name", "mode"):
        if not str(data.get(key) or "").strip():
            raise TaskConfigError(f"task config requires {key}: {config_path}")

    session = data.get("input") or {}
    if not isinstance(session, dict):
        raise TaskConfigError(f"task config input must be an object: {config_path}")
    file_ids = session.get("fileIds") or []
    if not isinstance(file_ids, list) or any(not isinstance(item, str) for item in file_ids):
        raise TaskConfigError(f"task config input.fileIds must be strings: {config_path}")

    selected = data.get("selectedSkills")
    if selected is not None:
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            raise TaskConfigError(f"task config selectedSkills must be strings: {config_path}")
        if len(selected) > MAX_SELECTED_SKILLS:
            raise TaskConfigError(
                f"task config selectedSkills supports at most {MAX_SELECTED_SKILLS} items"
            )

    try:
        policy = ExecutionPolicy.model_validate(data.get("executionPolicy") or {})
    except ValidationError as exc:
        raise TaskConfigError(f"invalid executionPolicy in {config_path}: {exc}") from exc

    mode_options: dict[str, Any] = data.get("modeOptions") or {}
    if not isinstance(mode_options, dict):
        raise TaskConfigError(f"task config modeOptions must be an object: {config_path}")

    return CreateTaskCommand(
        task_id=data.get("id"),
        name=str(data["name"]),
        mode=str(data["mode"]),
        goal=str(data["goal"]) if data.get("goal") else None,
        mode_options=mode_options,
        input_text=str(session.get("text") or ""),
        file_ids=list(file_ids),
        execution_policy=policy,
        workspace_id=data.get("workspaceId"),
        selected_skill_names=tuple(selected) if selected is not None else None,
    )


__all__ = ["TaskConfigError", "load_task_request"]

