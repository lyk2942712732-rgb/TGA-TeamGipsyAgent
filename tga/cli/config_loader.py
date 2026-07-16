"""Task config loading."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from tga.contracts import TGATask


class TaskConfigError(ValueError):
    """Raised when a task config cannot be read or validated."""


def load_task_config(path: str | Path) -> TGATask:
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

    data.setdefault("id", f"task_{uuid4().hex[:10]}")
    try:
        return TGATask.model_validate(data)
    except ValidationError as exc:
        raise TaskConfigError(f"invalid task config {config_path}: {exc}") from exc

