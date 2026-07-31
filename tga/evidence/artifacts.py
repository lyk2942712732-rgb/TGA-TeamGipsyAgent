"""Filesystem-backed artifact storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tga.contracts import ArtifactRecord


class ArtifactStore:
    def __init__(self, root: str | Path, *, execution_context=None):
        self.root = Path(root)
        self.execution_context = execution_context
        self.root.mkdir(parents=True, exist_ok=True)

    def save_text(
        self,
        *,
        task_id: str,
        intent_id: str | None,
        kind: str,
        text: str,
        tool: str | None = None,
        target: str | None = None,
        suffix: str = ".txt",
    ) -> ArtifactRecord:
        data = text.encode("utf-8", errors="replace")
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"artifact_{digest[:12]}"
        path = self.root / f"{artifact_id}{suffix}"
        self._atomic_write(path, data)
        return ArtifactRecord(
            id=artifact_id,
            task_id=task_id,
            intent_id=intent_id,
            kind=kind,  # type: ignore[arg-type]
            path=path.name,
            sha256=digest,
            tool=tool,
            target=target,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

    def save_bytes(
        self,
        *,
        task_id: str,
        intent_id: str | None,
        kind: str,
        data: bytes,
        tool: str | None = None,
        target: str | None = None,
        suffix: str = ".bin",
        identity_context: str | None = None,
    ) -> ArtifactRecord:
        """Persist an opaque response without decoding or logging it inline."""
        digest = hashlib.sha256(data).hexdigest()
        identity = hashlib.sha256(
            (identity_context.encode("utf-8", errors="replace") + b"\0" + data)
            if identity_context is not None else data
        ).hexdigest()
        artifact_id = f"artifact_{identity[:12]}"
        path = self.root / f"{artifact_id}{suffix}"
        self._atomic_write(path, data)
        return ArtifactRecord(
            id=artifact_id,
            task_id=task_id,
            intent_id=intent_id,
            kind=kind,  # type: ignore[arg-type]
            path=path.name,
            sha256=digest,
            tool=tool,
            target=target,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

    def read_text(self, artifact_id: str) -> str:
        matches = list(self.root.glob(f"{artifact_id}.*"))
        if not matches:
            return ""
        return matches[0].read_text(encoding="utf-8", errors="replace")

    def _atomic_write(self, path: Path, data: bytes) -> None:
        if self.execution_context is not None:
            self.execution_context.assert_active()
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if self.execution_context is not None:
                self.execution_context.assert_active()
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
