"""Per-Solver transcript persistence."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_TRANSCRIPT_TOOL_CONTENT_CHARS = 8_000


class TranscriptDivergenceError(RuntimeError):
    pass


class RepositorySolverTranscript:
    """Append-only transcript backed by the schema-v6 repository."""

    def __init__(
        self,
        *,
        repository,
        task_id: str,
        solver_id: str,
    ) -> None:
        self.repository = repository
        self.task_id = task_id
        self.solver_id = solver_id

    def read(self) -> list[dict[str, Any]]:
        return self._read_repository()

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


def _redact_message(message: dict[str, Any]) -> dict[str, Any]:
    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            value = re.sub(
                r"(?i)(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*[^\r\n]+",
                r"\1: [REDACTED]",
                value,
            )
            value = re.sub(
                r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
                "Bearer [REDACTED]",
                value,
            )
            return re.sub(
                r"(?i)\b(token|secret|api[_-]?key|password)\s*[=:]\s*[^\s,;}&]+",
                r"\1=[REDACTED]",
                value,
            )
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {
                key: scrub(item)
                for key, item in value.items()
                if not re.search(
                    r"authorization|cookie|token|secret|password|api[_-]?key",
                    str(key),
                    re.IGNORECASE,
                )
            }
        return value

    return scrub(message)


__all__ = [
    "MAX_TRANSCRIPT_TOOL_CONTENT_CHARS", "RepositorySolverTranscript",
    "TranscriptDivergenceError",
]
