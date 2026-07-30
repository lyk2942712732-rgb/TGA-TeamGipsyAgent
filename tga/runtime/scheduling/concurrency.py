"""Process-local concurrency and cooperative cancellation primitives."""

from __future__ import annotations

import threading


class CancellationError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            return True

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancellationError(self.reason or "cancelled")


class ConcurrencyLimiter:
    """Bounded process-local slots keyed by Task; durable leases remain authoritative."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1 or capacity > 2:
            raise ValueError("active Worker capacity must be between one and two")
        self.capacity = capacity
        self._lock = threading.Lock()
        self._active: dict[str, set[str]] = {}

    def acquire(self, task_id: str, solver_id: str) -> bool:
        with self._lock:
            active = self._active.setdefault(task_id, set())
            if solver_id in active:
                return False
            if len(active) >= self.capacity:
                return False
            active.add(solver_id)
            return True

    def release(self, task_id: str, solver_id: str) -> bool:
        with self._lock:
            active = self._active.get(task_id)
            if not active or solver_id not in active:
                return False
            active.remove(solver_id)
            if not active:
                self._active.pop(task_id, None)
            return True

    def active_count(self, task_id: str) -> int:
        with self._lock:
            return len(self._active.get(task_id, ()))


__all__ = [
    "CancellationError", "CancellationToken", "ConcurrencyLimiter",
]
