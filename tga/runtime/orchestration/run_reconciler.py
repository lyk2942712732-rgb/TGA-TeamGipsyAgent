"""Idempotently reconcile terminal SolverRuns into team lifecycle state."""

from __future__ import annotations

from tga.runtime.orchestration.solver_run_projector import SolverRunProjector


class SolverRunReconciler:
    def __init__(self, *, task, repositories) -> None:
        self.task = task
        self.repositories = repositories
        self.projector = SolverRunProjector(repositories=repositories)

    def reconcile(self, run) -> bool:
        if run.orchestration_role != "worker" or run.state not in {
            "completed", "failed", "cancelled", "expired", "waiting_approval"
        }:
            return False
        with self.repositories.transaction():
            current = self.repositories.orchestration.get_solver_run(run.id)
            if current is None:
                return False
            return self.projector.project(current)


__all__ = ["SolverRunReconciler"]
