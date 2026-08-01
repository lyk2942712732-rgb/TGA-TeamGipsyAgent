"""Project the authoritative SolverRun lifecycle into read models."""

from __future__ import annotations

from tga.evidence.database import utc_now


class SolverRunProjector:
    """The only runtime component that derives Solver/Intent state from a Run."""

    _SOLVER_STATUS = {
        "completed": "completed",
        "failed": "failed",
        "expired": "failed",
        "cancelled": "cancelled",
        "waiting_approval": "awaiting_approval",
    }
    _INTENT_STATUS = {
        "completed": "completed",
        "failed": "failed",
        "expired": "failed",
        "cancelled": "cancelled",
        "waiting_approval": "awaiting_approval",
    }
    _WORKER_RESULT_STATUS = {
        "succeeded": ("completed", "completed"),
        "partial": ("completed", "completed"),
        "blocked": ("blocked", "blocked"),
        "failed": ("failed", "failed"),
        "cancelled": ("cancelled", "cancelled"),
    }

    def __init__(self, *, repositories) -> None:
        self.repositories = repositories

    def project(
        self,
        run,
        *,
        lifecycle_state: str | None = None,
        complete_assignment: bool | None = None,
    ) -> bool:
        """Apply one idempotent terminal Run projection in the caller's UoW."""
        state = lifecycle_state or run.state
        if state not in self._SOLVER_STATUS:
            return False
        current = self.repositories.orchestration.get_solver_run(run.id)
        if current is None:
            return False
        solver = self.repositories.solvers.get_solver(current.solver_id)
        assignment = (
            self.repositories.orchestration.get_assignment(current.assignment_id)
            if current.assignment_id else None
        )
        plan = self.repositories.plans.get_global_plan(current.task_id)
        intent = next(
            (item for item in plan.intents if item.id == current.intent_id), None
        ) if plan and current.intent_id else None

        paused_generation = (
            state == "cancelled"
            and current.error_code in {"SOLVER_PAUSED", "TASK_PAUSED"}
        )
        solver_status = "paused" if paused_generation else self._SOLVER_STATUS[state]
        intent_status = "blocked" if paused_generation else self._INTENT_STATUS[state]

        if solver is not None and str(solver.status) not in {
            "completed", "failed", "cancelled"
        } and str(solver.status) != solver_status:
            self.repositories.solvers.update_solver_status(solver.id, solver_status)

        should_complete = (
            complete_assignment
            if complete_assignment is not None
            else state == "completed"
        )
        if assignment is not None and assignment.status in {"proposed", "accepted"}:
            finished_at = current.finished_at or utc_now()
            if should_complete:
                self.repositories.orchestration.complete_assignment(
                    assignment.id, finished_at=finished_at
                )
            elif state in {"failed", "expired", "cancelled"}:
                self.repositories.orchestration.cancel_assignment(
                    assignment.id, finished_at=finished_at
                )

        if intent is not None and intent.status not in {
            "completed", "failed", "cancelled"
        } and intent.status != intent_status:
            self.repositories.plans.update_intent_status(
                intent.id, intent_status, expected_status=intent.status
            )
        return True

    def project_started(self, run) -> bool:
        """Project a started Run into the Solver read model."""
        if run.state != "running":
            return False
        solver = self.repositories.solvers.get_solver(run.solver_id)
        if solver is None or str(solver.status) in {
            "completed", "failed", "cancelled", "running"
        }:
            return solver is not None
        self.repositories.solvers.update_solver_status(solver.id, "running")
        return True

    def project_worker_result(self, result) -> bool:
        """Project a structured WorkerResult before its Run is finalized.

        The model tool submits a result while the fenced SolverRun is still
        running.  Keeping this mapping here prevents ResultMerger from
        becoming a second lifecycle writer.
        """
        assignment = self.repositories.orchestration.get_assignment_for_solver(
            result.solver_id
        )
        solver = self.repositories.solvers.get_solver(result.solver_id)
        plan = self.repositories.plans.get_global_plan(result.task_id)
        if assignment is None or solver is None or plan is None:
            return False
        intent = next((item for item in plan.intents if item.id == result.intent_id), None)
        if intent is None:
            return False
        solver_status, intent_status = self.worker_result_statuses(result.status)
        if str(solver.status) not in {"completed", "failed", "cancelled"} and str(solver.status) != solver_status:
            self.repositories.solvers.update_solver_status(solver.id, solver_status)
        if intent.status not in {"completed", "failed", "cancelled"} and intent.status != intent_status:
            self.repositories.plans.update_intent_status(
                intent.id, intent_status, expected_status=intent.status
            )
        if assignment.status in {"proposed", "accepted"}:
            self.repositories.orchestration.complete_assignment(
                assignment.id, finished_at=utc_now()
            )
        return True

    @classmethod
    def worker_result_statuses(cls, status) -> tuple[str, str]:
        """Return the canonical Solver and Intent projections for a result."""
        return cls._WORKER_RESULT_STATUS[str(status)]


__all__ = ["SolverRunProjector"]
