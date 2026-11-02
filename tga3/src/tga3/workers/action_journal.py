"""Durable execution records, separate from files produced by tools."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ActionJournal:
    def __init__(self, directory: Path, *, identity: dict, secret: str = "", enabled: bool = True):
        self.directory, self.identity = directory, identity
        self.secret, self.enabled = secret, enabled
        self.pending: dict[str, tuple[Path, dict]] = {}

    def record(self, action_id: str, tool: str, *, inputs=None, result=None, status="started") -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        path, record = self.pending.get(action_id, (self.directory / f"action-{uuid4().hex}.json", {
            "schema_version": 1, **self.identity, "action_id": action_id,
            "tool": tool, "started_at": now,
        }))
        record = {**record, "updated_at": now, "status": status}
        if inputs is not None:
            record["input"] = inputs
        if result is not None:
            record["result"] = result
        content = json.dumps(record, ensure_ascii=False, indent=2)
        if self.secret:
            # Redact the JSON representation as well as ordinary literal occurrences.
            content = content.replace(json.dumps(self.secret, ensure_ascii=False)[1:-1], "[redacted]")
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                output.write(content + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        if status == "started":
            self.pending[action_id] = path, record
        else:
            self.pending.pop(action_id, None)
