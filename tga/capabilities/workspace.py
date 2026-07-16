"""Workspace helpers shared by the controlled capability runtime."""

from pathlib import Path


def resolve_solver_path(workspace: Path, relative_path: str) -> Path:
    root = workspace.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError("WORKSPACE_PATH_DENIED") from exc
    return candidate
