"""One atomic actions.json per agent; result bodies and attachments stay in its workspace."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..action_logs import action_log_name, read_action_log


class ActionJournal:
    def __init__(self, directory: Path, *, identity: dict, secret: str = "", enabled: bool = True,
                 output_directory: Path | None = None, snapshot_on_init: bool = True):
        self.directory = directory
        self.identity = {**identity, "agent_id": identity.get("agent_id") or "agent"}
        self.secret, self.enabled = secret, enabled
        self.agent_prefix = self.identity.get("agent_id") or "agent"
        self.manifest_path = self.directory / action_log_name(self.agent_prefix)
        self.output_directory = output_directory or directory.parent / "workspace"
        self.pending: dict[str, dict] = {}
        self.output_snapshots: dict[str, dict] = {}
        # Stream starts may arrive after a fast command has already written files.
        # Synchronous hooks instead capture their own baseline before execution.
        self.output_baseline = self._snapshot() if snapshot_on_init else None

    def _snapshot(self) -> dict:
        result = {}
        ignored = {".tga3", ".codex", ".codex-extract", ".claude", ".git"}
        for current, directories, names in os.walk(self.output_directory, followlinks=False):
            root = Path(current)
            directories[:] = [name for name in directories if name not in ignored
                               and not (root == self.output_directory and name == "result")
                               and not (root / name).is_symlink()
                               and (root / name).resolve() != self.directory.resolve()]
            for name in names:
                path = root / name
                try:
                    if (path.is_symlink() or name == "report-input.json" or
                            path == self.output_directory / 'final-candidate.json'):
                        continue
                    stat = path.stat()
                    result[path.relative_to(self.output_directory).as_posix()] = (
                        stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino,
                    )
                except OSError:
                    continue
        return result

    @contextmanager
    def _lock(self):
        # Lock a separate inode because actions.json itself is atomically replaced.
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".actions.lock").open("a+b") as lock:
            # Windows can lock beyond EOF. Initializing byte zero before taking the
            # lock races with another process that has already locked that byte.
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _write(self, path: Path, content: str) -> None:
        if self.secret:
            content = content.replace(self.secret, "[redacted]")
            content = content.replace(json.dumps(self.secret, ensure_ascii=False)[1:-1], "[redacted]")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_result(self, action_id: str, result, status: str, attachments: list[str]) -> str:
        body = result
        if isinstance(result, dict):
            if "aggregated_output" in result:
                body = result["aggregated_output"]
            elif result.get("type") == "tool_result":
                body = result.get("content", "")
            elif result.get("type") == "mcp_tool_call":
                body = result.get("result") if result.get("result") is not None else result
        if result is None:
            body = {"status": status, "result_received": False, "attachments": attachments}
        extension = ".txt" if isinstance(body, str) else ".json"
        relative = "result/" + action_id + extension
        self._write(self.output_directory / relative,
                    body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2) + "\n")
        return relative

    def record(self, tool_call_id: str, tool: str, *, inputs=None, result=None, status="started",
               cwd: str | None = None, before_execution: bool = False) -> dict | None:
        if not self.enabled:
            return
        now = datetime.now(UTC).isoformat()
        current_outputs = self._snapshot()
        if self.output_baseline is None:
            self.output_baseline = current_outputs
        record = self.pending.get(tool_call_id, {
            "action_id": self.agent_prefix + "-action-" + uuid4().hex, "tool_call_id": tool_call_id,
            "action": {"tool": tool}, "started_at": now,
            "artifact": {"output_path": None, "attachments": []},
        })
        record = {**record, "updated_at": now, "status": status}
        if inputs is not None:
            record["action"] = {"tool": tool, "input": inputs}
            if isinstance(inputs, dict) and inputs.get("command") is not None:
                record["action"]["command"] = inputs["command"]
        if cwd:
            record["action"]["cwd"] = cwd
        if status == "started":
            # Stream delivery may lag behind a very fast command's file writes.
            self.output_snapshots.setdefault(tool_call_id,
                                             current_outputs if before_execution else self.output_baseline)
        else:
            before = self.output_snapshots.pop(tool_call_id, self.output_baseline)
            attachments = sorted(path for path, value in current_outputs.items() if before.get(path) != value)
            record["artifact"] = {"output_path": self._save_result(record["action_id"], result, status, attachments),
                                  "attachments": attachments}
            record["exit_code"] = result.get("exit_code") if isinstance(result, dict) else None
        self.output_baseline = current_outputs
        with self._lock():
            document = read_action_log(self.directory, task_id=self.identity.get("task_id"),
                                       agent_id=self.identity.get("agent_id"))
            document.update({"schema_version": 2, **self.identity, "updated_at": now})
            index = next((i for i, entry in enumerate(document["actions"])
                          if entry["action_id"] == record["action_id"]), None)
            if index is None:
                document["actions"].append(record)
            else:
                document["actions"][index] = record
            self._write(self.manifest_path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        if status == "started":
            self.pending[tool_call_id] = record
        else:
            self.pending.pop(tool_call_id, None)
        return record

    def interrupt_pending(self) -> None:
        for tool_call_id, record in list(self.pending.items()):
            self.record(tool_call_id, record["action"]["tool"], status="interrupted")
