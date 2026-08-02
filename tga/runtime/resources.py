"""Resolve authorized task resources from the authoritative TaskSpec.

`TGATask.session_input` is unverified task material.  Authorization comes from
`TaskSpec.resources`, optionally narrowed by a SolverAssignment scope.  The
tool surface keeps addressing files by their immutable asset id, but the set of
addressable files is derived from TaskSpec, never from session input.
"""

from __future__ import annotations

from tga.domain.task.models import SessionFile, TGATask
from tga.domain.task.spec import TaskSpec


def authorized_session_files(
    task: TGATask,
    spec: TaskSpec | None,
    allowed_resource_ids: tuple[str, ...] | None = None,
) -> list[SessionFile]:
    """Return the staged files that TaskSpec authorizes for this scope."""
    if spec is None:
        return []
    authorized: set[str] = set()
    for resource in spec.resources:
        if allowed_resource_ids is not None and resource.id not in allowed_resource_ids:
            continue
        asset_id = str(resource.metadata.get("asset_id") or "")
        if asset_id:
            authorized.add(asset_id)
    return [item for item in task.session_input.files if item.id in authorized]


def authorized_asset_ids(
    spec: TaskSpec | None,
    allowed_resource_ids: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Tool-facing asset ids authorized by TaskSpec for this scope.

    Tools address inputs by their immutable asset id, while TaskSpec identifies
    resources by ResourceRef id.  This maps one to the other so authorization
    stays anchored to TaskSpec.
    """
    if spec is None:
        return ()
    values: list[str] = []
    for resource in spec.resources:
        if allowed_resource_ids is not None and resource.id not in allowed_resource_ids:
            continue
        asset_id = str(resource.metadata.get("asset_id") or "")
        if asset_id and asset_id not in values:
            values.append(asset_id)
    return tuple(values)


__all__ = ["authorized_asset_ids", "authorized_session_files"]
