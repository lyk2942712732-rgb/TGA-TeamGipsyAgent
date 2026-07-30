"""Lease-backed task and Solver scheduling units for local SQLite execution."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from tga.domain.solver import SolverLease, TaskOrchestratorLease
from tga.runtime.scheduling.concurrency import CancellationToken, ConcurrencyLimiter
from tga.runtime.scheduling.leases import SolverLeaseManager, TaskLeaseManager


@dataclass
class TaskRunContext:
    lease: TaskOrchestratorLease
    cancellation: CancellationToken
    _manager: TaskLeaseManager

    def assert_active(self) -> None:
        self.cancellation.raise_if_cancelled()
        if not self._manager.is_valid(self.lease):
            self.cancellation.cancel("task_orchestrator_lease_lost")
            self.cancellation.raise_if_cancelled()


@dataclass
class SolverRunContext:
    lease: SolverLease
    cancellation: CancellationToken
    _manager: SolverLeaseManager

    def assert_active(self) -> None:
        self.cancellation.raise_if_cancelled()
        if not self._manager.is_valid(self.lease):
            self.cancellation.cancel("solver_lease_lost")
            self.cancellation.raise_if_cancelled()


class _Heartbeat:
    def __init__(self, *, interval: float, renew: Callable[[], bool], token: CancellationToken):
        self.interval = interval
        self.renew = renew
        self.token = token
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=max(1.0, self.interval * 2))

    def _run(self) -> None:
        while not self.stopped.wait(self.interval):
            if not self.renew():
                self.token.cancel("lease_lost")
                return


class TaskScheduler:
    def __init__(
        self, *, repository_factory: Callable[[], Any], owner_id: str,
        lease_ttl_seconds: float = 60,
    ) -> None:
        self.repository_factory = repository_factory
        self.owner_id = owner_id
        self.lease_ttl_seconds = lease_ttl_seconds

    def run_once(self, task_id: str, operation: Callable[[TaskRunContext], Any]) -> bool:
        repositories = self.repository_factory()
        manager = TaskLeaseManager(repositories.orchestration)
        lease = manager.acquire(
            task_id, self.owner_id, ttl_seconds=self.lease_ttl_seconds
        )
        if lease is None:
            repositories.close()
            return False
        token = CancellationToken()
        context = TaskRunContext(lease=lease, cancellation=token, _manager=manager)

        def renew() -> bool:
            heartbeat_repositories = self.repository_factory()
            try:
                heartbeat_manager = TaskLeaseManager(
                    heartbeat_repositories.orchestration
                )
                renewed = heartbeat_manager.renew(
                    context.lease, ttl_seconds=self.lease_ttl_seconds
                )
                if renewed is None:
                    return False
                context.lease = renewed
                return True
            finally:
                heartbeat_repositories.close()

        heartbeat = _Heartbeat(
            interval=max(0.05, self.lease_ttl_seconds / 3), renew=renew, token=token
        )
        heartbeat.start()
        try:
            operation(context)
            context.assert_active()
            return True
        finally:
            heartbeat.close()
            manager.release(context.lease)
            repositories.close()


class SolverScheduler:
    def __init__(
        self, *, repository_factory: Callable[[], Any], owner_id: str,
        max_active_workers: int = 2, lease_ttl_seconds: float = 60,
        limiter: ConcurrencyLimiter | None = None,
    ) -> None:
        self.repository_factory = repository_factory
        self.owner_id = owner_id
        self.max_active_workers = max_active_workers
        self.lease_ttl_seconds = lease_ttl_seconds
        self.limiter = limiter or ConcurrencyLimiter(max_active_workers)

    def run_once(
        self, task_id: str, solver_id: str,
        operation: Callable[[SolverRunContext], Any],
    ) -> bool:
        if not self.limiter.acquire(task_id, solver_id):
            return False
        repositories = self.repository_factory()
        manager = SolverLeaseManager(repositories.solvers)
        lease = manager.acquire(
            task_id,
            solver_id,
            self.owner_id,
            ttl_seconds=self.lease_ttl_seconds,
            max_active_workers=self.max_active_workers,
        )
        if lease is None:
            repositories.close()
            self.limiter.release(task_id, solver_id)
            return False
        token = CancellationToken()
        context = SolverRunContext(lease=lease, cancellation=token, _manager=manager)

        def renew() -> bool:
            heartbeat_repositories = self.repository_factory()
            try:
                heartbeat_manager = SolverLeaseManager(
                    heartbeat_repositories.solvers
                )
                renewed = heartbeat_manager.renew(
                    context.lease, ttl_seconds=self.lease_ttl_seconds
                )
                if renewed is None:
                    return False
                context.lease = renewed
                return True
            finally:
                heartbeat_repositories.close()

        heartbeat = _Heartbeat(
            interval=max(0.05, self.lease_ttl_seconds / 3), renew=renew, token=token
        )
        heartbeat.start()
        try:
            operation(context)
            context.assert_active()
            return True
        finally:
            heartbeat.close()
            manager.release(context.lease)
            repositories.close()
            self.limiter.release(task_id, solver_id)


__all__ = [
    "SolverRunContext", "SolverScheduler", "TaskRunContext", "TaskScheduler",
]
