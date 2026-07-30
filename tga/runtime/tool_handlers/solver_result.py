"""Solver-result submission kept distinct from task completion."""

from __future__ import annotations

from tga.domain.solver.results import WorkerResult
from tga.infrastructure.persistence import PersistenceBundle


class SolverResultHandler:
    def __init__(self, repositories: PersistenceBundle) -> None:
        self.repositories = repositories

    def submit_solver_result(self, result: WorkerResult, *, version: int = 1) -> str:
        solver = self.repositories.solvers.get_solver(result.solver_id)
        if solver is None or solver.task_id != result.task_id:
            raise PermissionError("WorkerResult Solver ownership mismatch")
        if solver.completion_authority == "task":
            raise PermissionError("task supervisor uses propose_task_completion, not WorkerResult")
        return self.repositories.solvers.save_worker_result(result, version=version)


def submit_solver_result(
    repositories: PersistenceBundle, result: WorkerResult, *, version: int = 1
) -> str:
    return SolverResultHandler(repositories).submit_solver_result(result, version=version)


__all__ = ["SolverResultHandler", "submit_solver_result"]
