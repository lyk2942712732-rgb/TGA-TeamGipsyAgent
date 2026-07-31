"""Application-owned background scheduling for durable runtime sessions."""

from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.errors import RuntimeConfigurationError


_SENSITIVE_VALUE = re.compile(
    r"(?i)(authorization|proxy-authorization|cookie|set-cookie|token|secret|password|api[_-]?key)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")


class RuntimeScheduler:
    """Run at most one Manager loop per task and persist background failures."""

    def __init__(
        self,
        *,
        run_root: str | Path,
        run_task: Callable[[str], object],
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self.run_root = Path(run_root)
        self.run_task = run_task
        self.thread_factory = thread_factory
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._rerun_requested: set[str] = set()
        self._approval_timers: dict[str, tuple[str, str, threading.Timer]] = {}
        self.owner_id = f"scheduler_{uuid4().hex}"
        self.lease_ttl_seconds = 60.0

    def schedule(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._running:
                self._rerun_requested.add(task_id)
                return False
            self._running.add(task_id)
        if not self._acquire_lease(task_id):
            with self._lock:
                self._running.discard(task_id)
            return False
        try:
            thread = self.thread_factory(
                target=self._run,
                args=(task_id,),
                name=f"tga-runtime-{task_id}",
                daemon=True,
            )
            thread.start()
        except Exception:
            self._release_lease(task_id)
            with self._lock:
                self._running.discard(task_id)
                self._rerun_requested.discard(task_id)
            raise
        return True

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._running

    def recover(self, *, schedule_runnable: bool = True) -> list[str]:
        """Recover runnable Sessions and durable approval deadlines after restart."""
        scheduled: list[str] = []
        if not self.run_root.is_dir():
            return scheduled
        for db_path in self.run_root.glob("*/evidence.db"):
            task_id = db_path.parent.name
            try:
                store = EvidenceStore(db_path)
                try:
                    task = store.get_task(task_id)
                    session = store.get_session(task_id)
                    pending = next(
                        iter(PersistenceBundle(store).tool_governance.list_actions(
                            task_id, status="pending_approval", limit=1
                        )),
                        None,
                    )
                finally:
                    store.close()
            except Exception:
                continue
            if task is None or task.schema_version != 6 or session is None:
                continue
            if session.status == "awaiting_approval" or pending is not None:
                self._arm_approval_expiry(task_id)
            if session.status == "awaiting_approval":
                continue
            if not schedule_runnable:
                continue
            if session.status not in {"created", "running"}:
                continue
            if self.schedule(task_id):
                scheduled.append(task_id)
        return scheduled

    def _run(self, task_id: str) -> None:
        stopped = threading.Event()
        heartbeat = threading.Thread(
            target=self._renew_lease,
            args=(task_id, stopped),
            name=f"tga-lease-{task_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self.run_task(task_id)
        except Exception as exc:
            self._record_failure(task_id, exc)
        finally:
            stopped.set()
            heartbeat.join(timeout=1.0)
            self._release_lease(task_id)
            with self._lock:
                self._running.discard(task_id)
                rerun = task_id in self._rerun_requested
                self._rerun_requested.discard(task_id)
            # A control request may resume the durable Session while this
            # runner is still releasing its lease. Hand the task to a fresh
            # runner after the slot is free instead of dropping that request.
            if rerun:
                self.schedule(task_id)
            else:
                self._arm_approval_expiry(task_id)

    def _arm_approval_expiry(self, task_id: str) -> None:
        """Wake only when the durable pending action's deadline is reached."""
        db_path = self.run_root / task_id / "evidence.db"
        if not db_path.is_file():
            return
        store = EvidenceStore(db_path)
        try:
            session = store.get_session(task_id)
            governance = PersistenceBundle(store).tool_governance
            pending = next(
                (
                    item for item in governance.list_actions(
                        task_id, status="pending_approval", limit=1
                    )
                ),
                None,
            )
            approval = (
                governance.get_approval_for_action(str(pending.get("id") or ""))
                if pending is not None else None
            )
        finally:
            store.close()
        if (
            session is None
            or pending is None
        ):
            return
        action_id = str(pending.get("id") or "")
        raw_expiry = str((approval or {}).get("payload", {}).get("expires_at") or "")
        try:
            expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        except ValueError:
            return
        delay = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        replaced_timer: threading.Timer | None = None
        with self._lock:
            existing = self._approval_timers.get(task_id)
            if existing is not None:
                old_action_id, old_expiry, old_timer = existing
                if old_action_id == action_id and old_expiry == raw_expiry and old_timer.is_alive():
                    return
                old_timer.cancel()
                replaced_timer = old_timer
            timer = threading.Timer(delay, self._expire_approval, args=(task_id, action_id, raw_expiry))
            timer.daemon = True
            self._approval_timers[task_id] = (action_id, raw_expiry, timer)
            timer.start()
        # Timer.cancel() only signals the timer's Event. Join after releasing
        # _lock so a callback that already started can observe the replacement
        # and exit without deadlocking on this scheduler.
        if replaced_timer is not None and replaced_timer is not threading.current_thread():
            replaced_timer.join()

    def _expire_approval(self, task_id: str, action_id: str, raw_expiry: str) -> None:
        with self._lock:
            current = self._approval_timers.get(task_id)
            if current is None or current[:2] != (action_id, raw_expiry):
                return
            self._approval_timers.pop(task_id, None)
        db_path = self.run_root / task_id / "evidence.db"
        if not db_path.is_file():
            return
        store = EvidenceStore(db_path)
        try:
            expired = expire_pending_approvals(store, task_id)
        finally:
            store.close()
        if expired:
            self.schedule(task_id)
        else:
            # The decision may have raced this timer, or another approval may
            # now be pending. Re-read durable state before deciding to stop.
            self._arm_approval_expiry(task_id)

    def _acquire_lease(self, task_id: str) -> bool:
        db_path = self.run_root / task_id / "evidence.db"
        if not db_path.is_file():
            return False
        store = EvidenceStore(db_path)
        try:
            return store.acquire_runtime_lease(
                task_id, self.owner_id, ttl_seconds=self.lease_ttl_seconds
            )
        finally:
            store.close()

    def _renew_lease(self, task_id: str, stopped: threading.Event) -> None:
        while not stopped.wait(self.lease_ttl_seconds / 3):
            db_path = self.run_root / task_id / "evidence.db"
            try:
                store = EvidenceStore(db_path)
                try:
                    if not store.renew_runtime_lease(
                        task_id, self.owner_id, ttl_seconds=self.lease_ttl_seconds
                    ):
                        return
                finally:
                    store.close()
            except Exception:
                return

    def _release_lease(self, task_id: str) -> None:
        db_path = self.run_root / task_id / "evidence.db"
        if not db_path.is_file():
            return
        try:
            store = EvidenceStore(db_path)
            try:
                store.release_runtime_lease(task_id, self.owner_id)
            finally:
                store.close()
        except Exception:
            return

    def _record_failure(self, task_id: str, exc: Exception) -> None:
        db_path = self.run_root / task_id / "evidence.db"
        if not db_path.is_file():
            return
        store = EvidenceStore(db_path)
        try:
            session = store.get_session(task_id)
            if session is None or session.status in {"completed", "cancelled", "failed"}:
                return
            repositories = PersistenceBundle(store)
            orchestration = repositories.orchestration.get_state(task_id)
            solver_id = session.active_solver_id or (
                orchestration.supervisor_solver_id if orchestration is not None else None
            )
            message = _redact_error(str(exc))[:1000]
            if isinstance(exc, RuntimeConfigurationError):
                SessionCoordinator(store).stop(
                    task_id=task_id,
                    status="blocked",
                    reason="runtime_configuration_blocked",
                    solver_id=solver_id,
                    turn_count=session.turn_count,
                    error={
                        "code": exc.code,
                        "message": message,
                        "phase": exc.phase,
                        "retryable": exc.retryable,
                    },
                )
                self._mark_runtime_failure(
                    repositories, task_id, solver_id=solver_id,
                    solver_status="blocked", orchestration_status="blocked",
                )
                return
            SessionCoordinator(store).stop(
                task_id=task_id,
                status="failed",
                reason="background_runtime_failed",
                solver_id=solver_id,
                turn_count=session.turn_count,
                error={"code": "BACKGROUND_RUNTIME_FAILED", "message": message},
            )
            self._mark_runtime_failure(
                repositories, task_id, solver_id=solver_id,
                solver_status="failed", orchestration_status="failed",
            )
        finally:
            store.close()

    @staticmethod
    def _mark_runtime_failure(
        repositories: PersistenceBundle,
        task_id: str,
        *,
        solver_id: str | None,
        solver_status: str,
        orchestration_status: str,
    ) -> None:
        with repositories.transaction():
            if solver_id is not None:
                solver = repositories.solvers.get_solver(solver_id)
                if solver is not None and str(solver.status) not in {
                    "completed", "failed", "cancelled",
                }:
                    repositories.solvers.update_solver_status(
                        solver_id, solver_status
                    )
            state = repositories.orchestration.get_state(task_id)
            if state is not None and state.status not in {
                "completed", "failed", "cancelled",
            }:
                repositories.orchestration.save_state(state.model_copy(update={
                    "status": orchestration_status,
                    "updated_at": datetime.now(UTC).isoformat().replace(
                        "+00:00", "Z"
                    ),
                }))


def _redact_error(value: str) -> str:
    value = _SENSITIVE_VALUE.sub(r"\1=[REDACTED]", value)
    return _BEARER_VALUE.sub("Bearer [REDACTED]", value)
