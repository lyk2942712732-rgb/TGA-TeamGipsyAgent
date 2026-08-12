"""Validated task paths and immutable artifact publication."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path, PurePath

from tga2.core.models import Artifact


class TaskWorkspace:
    def __init__(self, run_root: str | Path, task_id: str) -> None:
        if (
            not task_id
            or task_id.strip() != task_id
            or PurePath(task_id).name != task_id
        ):
            raise ValueError("invalid task id")
        root = Path(run_root).resolve()
        self.root = (root / task_id).resolve()
        self.root.relative_to(root)
        self.inputs = self.root / "workspace" / "inputs"
        self.artifacts = self.root / "workspace" / "artifacts"
        self.reports = self.root / "reports"
        for path in (self.inputs, self.artifacts, self.reports):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.root / "state.db"

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "graph-checkpoints.db"

    def resolve_input(self, relative: str) -> Path:
        candidate = (self.inputs / relative).resolve()
        candidate.relative_to(self.inputs.resolve())
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        return candidate

    def list_inputs(self) -> list[str]:
        return sorted(
            path.relative_to(self.inputs).as_posix()
            for path in self.inputs.rglob("*")
            if path.is_file()
        )

    def publish_text(
        self,
        *,
        task_id: str,
        content: str,
        kind: str,
        tool_name: str | None = None,
        intent_id: str | None = None,
    ) -> tuple[Artifact, str]:
        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        filename = f"{digest[:16]}.txt"
        path = self.artifacts / filename
        if not path.exists():
            path.write_bytes(raw)
        artifact = Artifact(
            task_id=task_id,
            kind=kind,
            path=path.relative_to(self.root).as_posix(),
            sha256=digest,
            media_type="text/plain",
            tool_name=tool_name,
            intent_id=intent_id,
        )
        return artifact, content

    def ingest_input(self, source: str | Path) -> Path:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source)
        destination = self.inputs / source_path.name
        destination.write_bytes(source_path.read_bytes())
        return destination

    def read_artifact(self, relative: str) -> bytes:
        candidate = (self.root / relative).resolve()
        candidate.relative_to(self.artifacts.resolve())
        return candidate.read_bytes()

    @staticmethod
    def media_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


__all__ = ["TaskWorkspace"]
