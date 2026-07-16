"""Application service shared by FastAPI and CLI adapters."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.reporting.markdown_report import render_markdown_report
from tga.runtime.protocol import RUNTIME_SCHEMA_VERSION


class TaskRuntimeService:
    """Own task commands and queries without transport-specific behavior.

    The service never executes capabilities itself. Lifecycle mutations are
    delegated to Manager and all reads come from EvidenceStore.
    """

    def __init__(self, *, run_root: str | Path, manager: Any | None = None):
        self.run_root = Path(run_root)
        self._injected_manager = manager

    def task_root(self, task_id: str) -> Path:
        if not task_id or task_id.strip() != task_id:
            raise ValueError("invalid task id")
        root = self.run_root.resolve()
        candidate = (root / task_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("invalid task id") from exc
        return candidate

    def create_task(self, task: TGATask, *, initial_hint: str | None = None) -> dict[str, Any]:
        store = EvidenceStore(self.task_root(task.id) / "evidence.db")
        try:
            if store.task_snapshot(task.id).get("task"):
                raise ValueError("task id already exists")
            store.create_task(task)
        finally:
            store.close()
        result = self.command("start_session", task.id, initial_hint=initial_hint)
        return {"schema_version": RUNTIME_SCHEMA_VERSION, "task_id": task.id, **result}

    def run_task(self, task_id: str) -> dict[str, Any]:
        self.snapshot(task_id)
        return self._manager().run_session(task_id)

    def command(self, method_name: str, task_id: str, **payload: Any) -> dict[str, Any]:
        self.task_root(task_id)
        method = getattr(self._manager(), method_name)
        result = method(task_id=task_id, **payload)
        return result if isinstance(result, dict) else {"accepted": True, "status": "accepted"}

    def snapshot(self, task_id: str) -> dict[str, Any]:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        store = EvidenceStore(db_path)
        try:
            snapshot = store.get_session_snapshot(task_id)
        finally:
            store.close()
        if not snapshot.get("task") or not snapshot.get("session"):
            raise KeyError(f"runtime session not found: {task_id}")
        snapshot["schema_version"] = RUNTIME_SCHEMA_VERSION
        snapshot["latest_seq"] = max(
            (int(item.get("seq") or 0) for item in snapshot.get("agent_events") or []),
            default=0,
        )
        return snapshot

    def events(self, task_id: str, *, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        store = EvidenceStore(db_path)
        try:
            return [
                item.model_dump(mode="json")
                for item in store.list_events(task_id, after_seq=after_seq, limit=limit)
            ]
        finally:
            store.close()

    def list_tasks(self) -> list[dict[str, Any]]:
        if not self.run_root.exists():
            return []
        values: list[dict[str, Any]] = []
        for child in sorted(self.run_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
            if not child.is_dir() or child.name.startswith(".") or not (child / "evidence.db").is_file():
                continue
            try:
                snapshot = self.snapshot(child.name)
            except (KeyError, OSError, ValueError):
                continue
            task = snapshot["task"]
            session = snapshot["session"]
            events = snapshot.get("agent_events") or []
            solvers = snapshot.get("solvers") or []
            latest = events[-1] if events else None
            values.append({
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "task_id": child.name,
                "name": task.get("name") or child.name,
                "mode": task.get("mode") or "ctf",
                "target": task.get("target") or "",
                "created_at": events[0].get("created_at", "") if events else "",
                "updated_at": latest.get("created_at", "") if latest else "",
                "status": session.get("status") or "created",
                "turn_count": int(session.get("turn_count") or 0),
                "max_turns": int(session.get("max_turns") or 0),
                "active_solvers": sum(1 for item in solvers if item.get("status") in {"starting", "running", "waiting"}),
                "latest_event": {"seq": latest.get("seq"), "type": latest.get("type")} if latest else None,
                "flags": len(snapshot.get("flags") or []),
                "findings": len(snapshot.get("findings") or []),
                "artifacts": len(snapshot.get("artifacts") or []),
            })
        return values

    def delete_task(self, task_id: str) -> None:
        root = self.task_root(task_id)
        if (root / "evidence.db").is_file():
            snapshot = self.snapshot(task_id)
            if snapshot["session"]["status"] == "running":
                raise ValueError("running session cannot be deleted")
        if root.exists():
            shutil.rmtree(root)

    def write_report(self, task_id: str, *, output: str | Path | None = None) -> Path:
        snapshot = self.snapshot(task_id)
        path = Path(output) if output else self.task_root(task_id) / "reports" / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown_report(snapshot), encoding="utf-8")
        return path

    def _manager(self):
        if self._injected_manager is not None:
            return self._injected_manager
        from tga.runtime.manager import get_manager

        return get_manager()
