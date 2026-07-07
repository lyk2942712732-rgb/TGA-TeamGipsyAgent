"""Filesystem-backed artifact storage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from tga.contracts import ArtifactRecord


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
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
        path.write_bytes(data)
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

