"""Task helpers."""

from __future__ import annotations

from uuid import uuid4

from tga.contracts import TGATask


def new_task_id() -> str:
    return f"task_{uuid4().hex[:10]}"


def normalize_task(task: TGATask) -> TGATask:
    """Return a validated task with de-duplicated scope entries."""
    scope = []
    seen = set()
    for item in task.scope:
        clean = item.strip()
        if clean and clean not in seen:
            seen.add(clean)
            scope.append(clean)
    return task.model_copy(update={"scope": scope})

