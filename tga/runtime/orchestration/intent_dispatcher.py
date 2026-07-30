"""Dependency-aware serial Intent selection."""

from __future__ import annotations


ACTIVE_WORKER_STATUSES = {"queued", "running"}


class IntentDispatcher:
    def runnable(self, plan) -> list:
        statuses = {item.id: item.status for item in plan.intents}
        values = [
            item
            for item in plan.intents
            if item.status in {"pending", "ready"}
            and item.assigned_solver_id is None
            and all(
                statuses.get(dependency.intent_id) == dependency.required_status
                for dependency in item.dependencies
            )
        ]
        return sorted(values, key=lambda item: (-item.priority, item.created_at, item.id))

    def has_active_worker(self, solvers) -> bool:
        return self.active_worker_count(solvers) > 0

    def active_worker_count(self, solvers) -> int:
        return sum(
            item.orchestration_role == "worker"
            and str(item.status) in ACTIVE_WORKER_STATUSES
            for item in solvers
        )


__all__ = ["ACTIVE_WORKER_STATUSES", "IntentDispatcher"]
