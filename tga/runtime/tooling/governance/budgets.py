from threading import BoundedSemaphore

from pydantic import BaseModel, ConfigDict, Field


class TaskBudget(BaseModel):
    """Authoritative hard limits shared by every Solver in one Task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tool_calls: int | None = Field(default=None, ge=0)
    max_artifacts: int | None = Field(default=None, ge=0)
    max_turns: int | None = Field(default=None, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    max_total_tokens: int | None = Field(default=None, ge=0)
    max_artifact_bytes: int | None = Field(default=None, ge=0)
    max_total_solvers: int | None = Field(default=None, ge=1)
    max_active_workers: int | None = Field(default=None, ge=1, le=2)
    max_network_requests: int | None = Field(default=None, ge=0)
    max_network_requests_per_minute: int | None = Field(default=None, ge=0)
    max_network_concurrency: int | None = Field(default=None, ge=1)


class SolverBudget(TaskBudget):
    """A durable Solver allocation that can never enlarge its TaskBudget."""


class IntentBudget(TaskBudget):
    """A durable Intent allocation that can never enlarge its TaskBudget."""


class ProcessLocalConcurrencyGuard:
    """Fast process-local throttle; never an authoritative budget ledger."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("concurrency capacity must be positive")
        self._semaphore = BoundedSemaphore(capacity)

    def acquire(self, *, blocking: bool = True, timeout: float | None = None) -> bool:
        if not blocking:
            return self._semaphore.acquire(blocking=False)
        return self._semaphore.acquire(blocking=True, timeout=timeout)

    def release(self) -> None:
        self._semaphore.release()


class BudgetReservation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    task_id: str
    solver_id: str
    intent_id: str | None = None
    action_id: str
    status: str
    tool_calls: int
    artifacts: int


class BudgetService:
    """Persistent Solver/Intent reservation; Task remains the hard parent scope."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def reserve(self, action, *, tool_calls: int, artifacts: int) -> BudgetReservation:
        return BudgetReservation.model_validate(self.repository.reserve_budget(
            action, tool_calls=tool_calls, artifacts=artifacts
        ))

    def settle(self, reservation_id: str, *, artifacts: int | None = None) -> BudgetReservation:
        return BudgetReservation.model_validate(
            self.repository.settle_budget(reservation_id, artifacts=artifacts)
        )

    def release(self, reservation_id: str) -> None:
        self.repository.release_budget(reservation_id)


__all__ = [
    "BudgetReservation", "BudgetService", "IntentBudget",
    "ProcessLocalConcurrencyGuard", "SolverBudget", "TaskBudget",
]
