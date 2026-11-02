"""Durable execution records, separate from files produced by tools."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ActionJournal:
    def __init__(self, directory: Path, *, identity: dict, secret: str = "", enabled: bool = True,
                 output_directory: Path | None = None):
        self.directory, self.identity = directory, identity
        self.secret, self.enabled = secret, enabled
        self.pending: dict[str, tuple[Path, dict]] = {}
        self.output_directory = output_directory
        self.output_snapshots: dict[str, dict] = {}
        self.output_baseline = self._snapshot()

    def _snapshot(self) -> dict:
        result = {}
        if self.output_directory is not None:
            for path in self.output_directory.rglob("*"):
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    stat = path.stat()
                    result[path.relative_to(self.output_directory).as_posix()] = (
                        stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino,
                    )
                except OSError:
                    continue
        return result

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
        current_outputs = self._snapshot()
        if status == "started":
            # A fast command can write its file before its streamed start event is consumed.
            self.output_snapshots.setdefault(action_id, self.output_baseline)
            record.setdefault("output_paths", [])
        else:
            before = self.output_snapshots.pop(action_id, self.output_baseline)
            record["output_paths"] = sorted(
                path for path, fingerprint in current_outputs.items() if before.get(path) != fingerprint
            )
        self.output_baseline = current_outputs
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

    def interrupt_pending(self) -> None:
        """Keep paths to partial output files even when the CLI never emitted tool completion."""
        for action_id, (_, record) in list(self.pending.items()):
            self.record(action_id, record["tool"], status="interrupted")
