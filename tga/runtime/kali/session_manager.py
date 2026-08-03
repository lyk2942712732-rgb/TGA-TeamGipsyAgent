"""Owned, bounded interactive Kali sessions."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tga.domain.kali import KaliSessionArguments
from tga.sandbox.models import ProcessSpec, SandboxHandle
from tga.sandbox.provider import SandboxError, SandboxProcess


@dataclass(slots=True)
class _Session:
    id: str
    task_id: str
    solver_id: str
    solver_run_id: str
    fencing_token: int
    handle: SandboxHandle
    process: SandboxProcess
    created_at: float = field(default_factory=time.monotonic)
    touched_at: float = field(default_factory=time.monotonic)
    closed: bool = False


class KaliSessionManager:
    def __init__(
        self,
        manager,
        *,
        idle_timeout_seconds: int = 900,
        max_sessions_per_run: int = 4,
    ) -> None:
        self.manager = manager
        self.idle_timeout_seconds = idle_timeout_seconds
        self.max_sessions_per_run = max_sessions_per_run
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def execute(
        self,
        *,
        request,
        handle: SandboxHandle,
        profile,
        workspace: Path,
    ) -> dict[str, Any]:
        args = KaliSessionArguments.model_validate(request.arguments)
        with self._lock:
            self._close_idle_locked()
            if args.operation == "open":
                return self._open(request, handle, profile, workspace, args)
            session = self._owned(request, args.session_id or "")
            if args.operation == "write":
                session.process.send((args.input or "").encode("utf-8"))
                session.touched_at = time.monotonic()
                return {"session_id": session.id, "operation": "write", "written_chars": len(args.input or "")}
            if args.operation == "read":
                return self._read(session, args.wait_ms, args.max_output_chars)
            if args.operation == "resize":
                session.process.resize(args.cols or 80, args.rows or 24)
                session.touched_at = time.monotonic()
                return {"session_id": session.id, "operation": "resize", "cols": args.cols, "rows": args.rows}
            self._close_locked(session.id)
            return {"session_id": session.id, "operation": "close", "closed": True}

    def close_run(self, task_id: str, solver_id: str, solver_run_id: str) -> int:
        with self._lock:
            ids = [
                item.id for item in self._sessions.values()
                if item.task_id == task_id
                and item.solver_id == solver_id
                and item.solver_run_id == solver_run_id
            ]
            for session_id in ids:
                self._close_locked(session_id)
            return len(ids)

    def close_task(self, task_id: str) -> int:
        with self._lock:
            ids = [item.id for item in self._sessions.values() if item.task_id == task_id]
            for session_id in ids:
                self._close_locked(session_id)
            return len(ids)

    def _open(self, request, handle, profile, workspace: Path, args) -> dict[str, Any]:
        executable = str(args.executable)
        if executable not in profile.session_executables:
            raise SandboxError(
                f"executable {executable!r} is not session-enabled by profile {profile.id}",
                code="SESSION_EXECUTABLE_NOT_ALLOWED",
            )
        active = sum(
            1 for item in self._sessions.values()
            if item.task_id == request.task_id
            and item.solver_id == request.solver_id
            and item.solver_run_id == request.solver_run_id
        )
        if active >= self.max_sessions_per_run:
            raise SandboxError("Kali session limit reached", code="SESSION_LIMIT_REACHED")
        cwd = _relative_workspace_path(workspace, args.cwd)
        process = self.manager.open_process(
            handle,
            ProcessSpec(
                argv=(executable, *args.argv),
                tool_id="kali.session",
                working_directory=cwd,
                timeout_seconds=profile.limits.timeout_seconds,
                interactive=True,
            ),
        )
        session_id = f"ks_{uuid.uuid4().hex}"
        self._sessions[session_id] = _Session(
            id=session_id,
            task_id=request.task_id,
            solver_id=request.solver_id,
            solver_run_id=str(request.solver_run_id),
            fencing_token=int(request.fencing_token),
            handle=handle,
            process=process,
        )
        return {
            "session_id": session_id,
            "operation": "open",
            "executable": executable,
            "cols": args.cols or 80,
            "rows": args.rows or 24,
        }

    def _owned(self, request, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            raise SandboxError("Kali session does not exist", code="SESSION_NOT_FOUND")
        owner = (
            request.task_id,
            request.solver_id,
            request.solver_run_id,
            request.fencing_token,
        )
        expected = (
            session.task_id,
            session.solver_id,
            session.solver_run_id,
            session.fencing_token,
        )
        if owner != expected:
            raise SandboxError("Kali session is owned by another SolverRun", code="SESSION_OWNER_MISMATCH")
        return session

    def _read(self, session: _Session, wait_ms: int, max_chars: int) -> dict[str, Any]:
        deadline = time.monotonic() + wait_ms / 1000
        chunks: list[str] = []
        size = 0
        closed = False
        while size < max_chars:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and chunks:
                break
            try:
                frame = session.process.receive(max(0.001, remaining))
            except TimeoutError:
                break
            except SandboxError as exc:
                if exc.code != "PROCESS_STREAM_CLOSED":
                    raise
                closed = True
                self._close_locked(session.id)
                break
            text = frame.data.decode("utf-8", errors="replace")
            available = max_chars - size
            chunks.append(text[:available])
            size += min(len(text), available)
        session.touched_at = time.monotonic()
        return {
            "session_id": session.id,
            "operation": "read",
            "output": "".join(chunks),
            "closed": closed,
            "truncated": size >= max_chars,
        }

    def _close_idle_locked(self) -> None:
        cutoff = time.monotonic() - self.idle_timeout_seconds
        for session_id in [
            item.id for item in self._sessions.values() if item.touched_at <= cutoff
        ]:
            self._close_locked(session_id)

    def _close_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        session.process.close()


def _relative_workspace_path(workspace: Path, value: str) -> str:
    normalized = value.replace("\\", "/").strip("/") or "."
    candidate = (workspace / normalized).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("Kali working directory escapes the SolverRun workspace") from exc
    if normalized != "." and not candidate.is_dir():
        candidate.mkdir(parents=True, exist_ok=True)
    return normalized


__all__ = ["KaliSessionManager"]
