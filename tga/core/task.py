"""Task helpers."""

from __future__ import annotations

from uuid import uuid4

from tga.contracts import TGATask
from tga.network_policy import normalize_origin


def new_task_id() -> str:
    return f"task_{uuid4().hex[:10]}"


def normalize_task(task: TGATask) -> TGATask:
    """Return a validated task with canonical, de-duplicated network origins."""
    policy = task.execution_policy.model_copy(deep=True)
    policy.network.seed_origins = list(
        dict.fromkeys(normalize_origin(item) for item in policy.network.seed_origins)
    )
    policy.network.custom_origins = list(
        dict.fromkeys(normalize_origin(item) for item in policy.network.custom_origins)
    )
    return task.model_copy(update={"execution_policy": policy})

