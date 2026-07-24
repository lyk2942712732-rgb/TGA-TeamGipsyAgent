"""Durable transcript storage for model interactions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4


class TranscriptStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def append(self, message: dict[str, Any]) -> None:
        messages = self.read()
        messages.append(_redact_message(message))
        self.save(messages)

    def save(self, messages: list[dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(f".{os.getpid()}.{uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps([_redact_message(item) for item in messages], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


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
