"""Application boundary for task lifecycle commands."""

from __future__ import annotations

from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.coordinator import SessionCoordinator, SessionOutcome
from tga.runtime.orchestration import TaskOrchestrator


class TaskLifecycleService:
    """Apply one task command and update the compatibility Session projection."""

    def __init__(self, *, task, store) -> None:
        self.task = task
        self.store = store
        self.repositories = PersistenceBundle(store)
        self.orchestrator = TaskOrchestrator(
            task=task,
            repositories=self.repositories,
        )
        self.coordinator = SessionCoordinator(store)

    def pause(self, *, reason: str = "user_paused"):
        with self.repositories.transaction():
            state = self.orchestrator.pause(reason=reason)
            session = self.coordinator.pause(task_id=self.task.id, reason=reason)
            self._assert_projection(state.status, session.status)
            return session

    def resume(self, *, reason: str = "user_resumed"):
        with self.repositories.transaction():
            state = self.orchestrator.resume()
            session = self.coordinator.resume(task_id=self.task.id, reason=reason)
            self._assert_projection(state.status, session.status)
            return session

    def cancel(self, *, reason: str = "user_cancelled"):
        with self.repositories.transaction():
            state = self.orchestrator.cancel(reason=reason)
            session = self.coordinator.cancel(task_id=self.task.id, reason=reason)
            self._assert_projection(state.status, session.status)
            return session

    def block(
        self,
        *,
        reason: str,
        turn_count: int | None = None,
        solver_id: str | None = None,
        error: dict | None = None,
        solver_status: str = "paused",
    ):
        with self.repositories.transaction():
            state = self.orchestrator.block(
                reason=reason, solver_status=solver_status
            )
            current = self.store.get_session(self.task.id)
            session = current if current and current.status == "blocked" else self.coordinator.block(
                task_id=self.task.id,
                reason=reason,
                turn_count=turn_count,
                solver_id=solver_id,
                error=error,
            )
            self._assert_projection(state.status, session.status)
            return session

    def fail(
        self,
        *,
        reason: str,
        turn_count: int | None = None,
        solver_id: str | None = None,
        error: dict | None = None,
    ):
        with self.repositories.transaction():
            state = self.orchestrator.fail(reason=reason)
            current = self.store.get_session(self.task.id)
            session = current if current and current.status == "failed" else self.coordinator.fail(
                task_id=self.task.id,
                reason=reason,
                turn_count=turn_count,
                solver_id=solver_id,
                error=error,
            )
            self._assert_projection(state.status, session.status)
            return session

    def apply(self, *, solver_id: str, outcome: SessionOutcome):
        if outcome.status == "blocked":
            return self.block(
                reason=outcome.stop_reason,
                turn_count=outcome.turn_count,
                solver_id=solver_id,
                error=outcome.error,
            )
        if outcome.status == "failed":
            return self.fail(
                reason=outcome.stop_reason,
                turn_count=outcome.turn_count,
                solver_id=solver_id,
                error=outcome.error,
            )
        if outcome.status == "cancelled":
            return self.cancel(reason=outcome.stop_reason or "cancelled")
        raise ValueError(f"unsupported task lifecycle outcome: {outcome.status}")

    @staticmethod
    def _assert_projection(orchestrator_status: str, session_status: str) -> None:
        mapped = {
            "awaiting_input": "blocked",
            "running": "running",
            "paused": "paused",
            "blocked": "blocked",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }
        if mapped.get(orchestrator_status) != session_status:
            raise RuntimeError(
                "task lifecycle projection diverged: "
                f"orchestrator={orchestrator_status} session={session_status}"
            )


__all__ = ["TaskLifecycleService"]
