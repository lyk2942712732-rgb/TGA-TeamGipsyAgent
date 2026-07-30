"""Runtime lease managers that carry fencing tokens across runner boundaries."""

from __future__ import annotations

from datetime import datetime

from tga.domain.solver import SolverLease, TaskOrchestratorLease


class SolverLeaseManager:
    def __init__(self, repository) -> None:
        self.repository = repository

    def acquire(
        self,
        task_id: str,
        solver_id: str,
        owner_id: str,
        *,
        ttl_seconds: float,
        now: datetime | None = None,
        max_active_workers: int | None = None,
    ) -> SolverLease | None:
        return self.repository.acquire_lease_handle(
            task_id,
            solver_id,
            owner_id,
            ttl_seconds=ttl_seconds,
            now=now,
            max_active_workers=max_active_workers,
        )

    def renew(
        self, lease: SolverLease, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> SolverLease | None:
        return self.repository.renew_lease_handle(
            lease, ttl_seconds=ttl_seconds, now=now
        )

    def is_valid(
        self, lease: SolverLease, *, now: datetime | None = None
    ) -> bool:
        return self.repository.validate_lease(lease, now=now)

    def release(self, lease: SolverLease) -> bool:
        return self.repository.release_lease_handle(lease)


class TaskLeaseManager:
    def __init__(self, repository) -> None:
        self.repository = repository

    def acquire(
        self, task_id: str, owner_id: str, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> TaskOrchestratorLease | None:
        return self.repository.acquire_task_lease(
            task_id, owner_id, ttl_seconds=ttl_seconds, now=now
        )

    def renew(
        self, lease: TaskOrchestratorLease, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> TaskOrchestratorLease | None:
        return self.repository.renew_task_lease(
            lease, ttl_seconds=ttl_seconds, now=now
        )

    def is_valid(
        self, lease: TaskOrchestratorLease, *, now: datetime | None = None
    ) -> bool:
        return self.repository.validate_task_lease(lease, now=now)

    def release(self, lease: TaskOrchestratorLease) -> bool:
        return self.repository.release_task_lease(lease)


__all__ = ["SolverLeaseManager", "TaskLeaseManager"]
