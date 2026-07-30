"""Per-Solver writable workspaces and immutable shared Artifact publication."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from tga.domain.evidence import Artifact
from tga.evidence.database import utc_now


@dataclass(frozen=True)
class SolverWorkspace:
    task_root: Path
    solver_id: str

    @property
    def root(self) -> Path:
        return self.task_root / "workspace" / "solvers" / self.solver_id

    @property
    def scratch(self) -> Path:
        return self.root / "scratch"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    def ensure(self) -> "SolverWorkspace":
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        return self

    def write_text(self, relative_path: str, content: str) -> Path:
        target = self._write_target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_replace(target, content.encode("utf-8"))
        return target

    def _write_target(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        posix = PurePosixPath(normalized)
        if (
            not normalized
            or posix.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or ".." in posix.parts
            or not posix.parts
            or posix.parts[0] not in {"scratch", "outputs"}
        ):
            raise PermissionError("Solver writes must remain under scratch/ or outputs/")
        target = (self.root / Path(*posix.parts)).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as exc:
            raise PermissionError("Solver workspace path escapes its owner") from exc
        return target

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


class SolverWorkspaceService:
    def __init__(self, task_root: str | Path, *, budget_manager=None) -> None:
        self.task_root = Path(task_root).resolve()
        self.budget_manager = budget_manager
        self.inputs = self.task_root / "workspace" / "inputs"
        self.shared_artifacts = self.task_root / "workspace" / "shared" / "artifacts"
        self.inputs.mkdir(parents=True, exist_ok=True)
        self.shared_artifacts.mkdir(parents=True, exist_ok=True)

    def for_solver(self, solver_id: str) -> SolverWorkspace:
        if not solver_id or any(char in solver_id for char in "/\\"):
            raise ValueError("invalid Solver workspace identity")
        return SolverWorkspace(self.task_root, solver_id).ensure()

    def publish_artifact(
        self,
        *,
        task_id: str,
        solver_id: str,
        intent_id: str | None,
        data: bytes,
        suffix: str = ".bin",
        kind: str = "solver_output",
        media_type: str | None = None,
    ) -> Artifact:
        self.for_solver(solver_id)
        if not suffix.startswith(".") or any(char in suffix for char in "/\\"):
            raise ValueError("invalid Artifact suffix")
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"artifact_{digest[:24]}"
        if self.budget_manager is not None:
            self.budget_manager.record_usage(
                idempotency_key=f"artifact:{task_id}:{digest}",
                task_id=task_id,
                solver_id=solver_id,
                intent_id=intent_id,
                artifact_bytes=len(data),
            )
        destination = self.shared_artifacts / f"{artifact_id}{suffix}"
        if destination.exists():
            if destination.read_bytes() != data:
                raise RuntimeError("immutable Artifact identity collision")
        else:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp",
                dir=self.shared_artifacts,
            )
            temporary_path = Path(temporary)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary_path, destination)
                except FileExistsError:
                    if destination.read_bytes() != data:
                        raise RuntimeError("immutable Artifact publication conflict")
            finally:
                temporary_path.unlink(missing_ok=True)
        return Artifact(
            id=artifact_id,
            task_id=task_id,
            intent_id=intent_id,
            kind=kind,
            path=destination.name,
            sha256=digest,
            media_type=media_type,
            tool="publish_artifact",
            target=f"solver://{solver_id}/outputs",
            created_at=utc_now(),
            provenance={"published_by_solver_id": solver_id, "immutable": True},
        )


__all__ = ["SolverWorkspace", "SolverWorkspaceService"]
