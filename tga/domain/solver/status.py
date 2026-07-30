"""Solver-instance and worker-result lifecycle vocabulary."""

from enum import StrEnum


class SolverInstanceStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


__all__ = ["SolverInstanceStatus", "WorkerResultStatus"]
