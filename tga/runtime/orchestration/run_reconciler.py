"""Idempotently reconcile terminal SolverRuns into team lifecycle state."""

from __future__ import annotations

from tga.evidence.database import utc_now


class SolverRunReconciler:
    def __init__(self, *, task, repositories) -> None:
        self.task = task
        self.repositories = repositories

    def reconcile(self, run) -> bool:
        if run.orchestration_role != "worker" or run.state not in {
            "completed", "failed", "cancelled", "expired", "waiting_approval"
        }:
            return False
        with self.repositories.transaction():
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
                current.state == "cancelled"
                and current.error_code in {"SOLVER_PAUSED", "TASK_PAUSED"}
            )

            solver_status = {
                "completed": "completed",
                "failed": "failed",
                "expired": "failed",
                "cancelled": "cancelled",
                "waiting_approval": "awaiting_approval",
            }[current.state]
            intent_status = {
                "completed": "completed",
                "failed": "failed",
                "expired": "failed",
                "cancelled": "cancelled",
                "waiting_approval": "awaiting_approval",
            }[current.state]
            if paused_generation:
                solver_status = "paused"
                intent_status = "blocked"
            if solver is not None and str(solver.status) not in {
                "completed", "failed", "cancelled"
            } and str(solver.status) != solver_status:
                self.repositories.solvers.update_solver_status(solver.id, solver_status)
            if assignment is not None and assignment.status in {"proposed", "accepted"}:
                if current.state == "completed":
                    self.repositories.orchestration.complete_assignment(
                        assignment.id, finished_at=current.finished_at or utc_now()
                    )
                elif current.state in {"failed", "expired", "cancelled"}:
                    self.repositories.orchestration.cancel_assignment(
                        assignment.id, finished_at=current.finished_at or utc_now()
                    )
            if intent is not None and intent.status not in {
                "completed", "failed", "cancelled"
            } and intent.status != intent_status:
                self.repositories.plans.update_intent_status(
                    intent.id, intent_status, expected_status=intent.status
                )
            return True


__all__ = ["SolverRunReconciler"]
