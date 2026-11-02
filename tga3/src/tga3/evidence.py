"""Resolve agent-relative evidence paths and register file metadata on the server."""

import hashlib
import mimetypes
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid5

from .blackboard import Blackboard
from .domain import Actor, Artifact, ArtifactRef
from .errors import ContractError


async def register_evidence(
    board: Blackboard, task_id: UUID, actor: Actor, root: Path, paths: list[str],
) -> list[ArtifactRef]:
    if root.resolve() != root.absolute():
        raise ContractError("Evidence directory cannot be a symlink to another directory")
    root = root.resolve()
    files = {}
    # Validate the entire request before creating any evidence records.
    for relative in paths:
        if (not relative.strip() or "\x00" in relative or "\\" in relative
                or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
                or ".." in PurePosixPath(relative).parts
                or (PurePosixPath(relative).parts and PurePosixPath(relative).parts[0]
                    in {".codex", ".claude", ".tga3", ".codex-extract"})):
            raise ContractError(f"Evidence path must be relative and cannot escape the agent directory: {relative}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ContractError(f"Evidence is not a file inside the agent directory: {relative}")
        canonical = path.relative_to(root).as_posix()
        if canonical in files:
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        files[canonical] = Artifact(
            id=uuid5(task_id, f"{actor.agent_id}:{canonical}:{digest}"),
            task_id=task_id, created_by=actor, name=canonical, storage_path=str(path),
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            sha256=digest, size_bytes=len(content),
        )
    references = []
    for relative, evidence in files.items():
        registered = await board.register_artifact(evidence)
        references.append(ArtifactRef(artifact_id=registered.id, locator=relative))
    return references
