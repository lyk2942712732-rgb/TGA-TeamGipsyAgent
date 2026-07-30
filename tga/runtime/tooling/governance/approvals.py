"""Solver/Intent-scoped approval coordination; Task Session stays runnable."""

from tga.infrastructure.persistence import PersistenceBundle


class SolverApprovalCoordinator:
    def __init__(self, store) -> None:
        self.repositories = PersistenceBundle(store)

    def await_approval(self, *, solver_id: str, intent_id: str | None) -> None:
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is not None and str(solver.status) != "awaiting_approval":
            self.repositories.solvers.update_solver_status(
                solver_id, "awaiting_approval"
            )
        if intent_id:
            plan = self.repositories.plans.get_global_plan(solver.task_id if solver else "")
            intent = next((item for item in plan.intents if item.id == intent_id), None) if plan else None
            if intent is not None and intent.status != "awaiting_approval":
                old_version = plan.version
                self.repositories.plans.update_intent_status(
                    intent_id, "awaiting_approval", expected_status=intent.status
                )
                self.repositories.events.append_agent_event(
                    solver.task_id,
                    "PLAN_UPDATED",
                    {
                        "operation": "intent_awaiting_approval",
                        "intent_id": intent_id,
                        "old_version": old_version,
                        "new_version": old_version + 1,
                    },
                    solver_id=solver_id,
                )

    def resolve(self, *, solver_id: str, intent_id: str | None) -> None:
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is not None and str(solver.status) == "awaiting_approval":
            self.repositories.solvers.update_solver_status(solver_id, "ready")
        if intent_id:
            plan = self.repositories.plans.get_global_plan(solver.task_id if solver else "")
            intent = next((item for item in plan.intents if item.id == intent_id), None) if plan else None
            if intent is not None and intent.status == "awaiting_approval":
                old_version = plan.version
                self.repositories.plans.update_intent_status(
                    intent_id, "running", expected_status="awaiting_approval"
                )
                self.repositories.events.append_agent_event(
                    solver.task_id,
                    "PLAN_UPDATED",
                    {
                        "operation": "intent_approval_resolved",
                        "intent_id": intent_id,
                        "old_version": old_version,
                        "new_version": old_version + 1,
                    },
                    solver_id=solver_id,
                )


__all__ = ["SolverApprovalCoordinator"]
