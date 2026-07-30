"""Per-Solver transcript persistence and compatibility mirroring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tga.runtime.transcript import TranscriptStore, _redact_message


MAX_TRANSCRIPT_TOOL_CONTENT_CHARS = 8_000


class TranscriptDivergenceError(RuntimeError):
    pass


class RepositorySolverTranscript:
    """Append through TranscriptRepository; optionally mirror the legacy JSON file."""

    def __init__(
        self,
        *,
        repository,
        task_id: str,
        solver_id: str,
        mirror_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.task_id = task_id
        self.solver_id = solver_id
        self.mirror = TranscriptStore(mirror_path) if mirror_path is not None else None
        if not self._read_repository() and self.mirror is not None:
            legacy = self.mirror.read()
            if legacy:
                self.save(legacy)

    def read(self) -> list[dict[str, Any]]:
        values = self._read_repository()
        if values:
            return values
        return self.mirror.read() if self.mirror is not None else []

    def append(self, message: dict[str, Any]) -> None:
        self.save([*self.read(), message])

    def save(self, messages: list[dict[str, Any]]) -> None:
        normalized = [_normalize_message(item) for item in messages]
        existing = self._read_repository()
        if existing != normalized[: len(existing)]:
            raise TranscriptDivergenceError(
                "transcript rewrite would change already persisted Solver messages"
            )
        for message in normalized[len(existing):]:
            self.repository.append_message(self.task_id, self.solver_id, message)
        if self.mirror is not None:
            self.mirror.save(normalized)

    def _read_repository(self) -> list[dict[str, Any]]:
        return [
            {
                key: value
                for key, value in item.items()
                if key not in {"seq", "created_at"}
            }
            for item in self.repository.list_messages(self.task_id, self.solver_id)
        ]


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    value = _redact_message(dict(message))
    if value.get("role") != "tool":
        return value
    content = str(value.get("content") or "")
    if len(content) <= MAX_TRANSCRIPT_TOOL_CONTENT_CHARS:
        return value
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {"summary": content[:1_200]}
    if not isinstance(payload, dict):
        payload = {"summary": str(payload)[:1_200]}
    compact = {
        key: payload[key]
        for key in (
            "ok", "status", "summary", "artifact_id", "artifact_ids", "artifacts", "error"
        )
        if key in payload
    }
    compact.update({
        "transcript_compacted": True,
        "original_chars": len(content),
        "instruction": "Retrieve full content from the immutable Artifact by ID.",
    })
    value["content"] = json.dumps(compact, ensure_ascii=False)
    return value


__all__ = [
    "MAX_TRANSCRIPT_TOOL_CONTENT_CHARS", "RepositorySolverTranscript",
    "TranscriptDivergenceError",
]
