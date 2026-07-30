"""Task-domain lifecycle vocabulary."""

from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

