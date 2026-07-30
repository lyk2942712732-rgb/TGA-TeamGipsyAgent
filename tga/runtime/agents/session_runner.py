"""One-Solver runner boundary with the legacy class retained behind an adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tga.infrastructure.persistence import PersistenceBundle


@dataclass(frozen=True)
class SolverOutcome:
    task_id: str
    solver_id: str
    status: str
    stop_reason: str = ""
    turn_count: int = 0
    summary: str = ""
    evidence_artifact_ids: list[str] | None = None
    error: dict[str, Any] | None = None
    details: dict[str, Any] | None = None


class SolverRunner:
    """Load one SolverInstance, then run exactly that Solver's model loop."""

    def __init__(self, **kwargs: Any) -> None:
        self.task = kwargs["task"]
        self.solver_id = str(kwargs["solver_id"])
        store = kwargs["store"]
        self.repositories = PersistenceBundle(store)
        solver = self.repositories.solvers.get_solver(self.solver_id)
        if solver is None or solver.task_id != self.task.id:
            raise RuntimeError(
                f"durable SolverInstance is missing or mis-owned: {self.solver_id}"
            )
        # Compatibility execution engine. It is scoped to this Solver and is
        # no longer responsible for choosing or creating the durable identity.
        from tga.runtime.agent_session import AgentSessionRunner

        self._engine = AgentSessionRunner(**kwargs)

    def run(self) -> SolverOutcome:
        self._set_status("running")
        try:
            outcome = self._engine.run()
        except Exception:
            self._set_status("failed")
            raise
        result = SolverOutcome(
            task_id=self.task.id,
            solver_id=self.solver_id,
            status=outcome.status,
            stop_reason=outcome.stop_reason,
            turn_count=outcome.turn_count,
            summary=outcome.summary,
            evidence_artifact_ids=outcome.evidence_artifact_ids,
            error=outcome.error,
            details=outcome.details,
        )
        status = {
            "completed": "completed",
            "cancelled": "cancelled",
            "failed": "failed",
            "blocked": "blocked",
            "awaiting_approval": "awaiting_approval",
            "paused": "waiting",
            "running": "waiting",
        }.get(result.status, "blocked")
        self._set_status(status)
        return result

    def _set_status(self, status: str) -> None:
        before = self.repositories.solvers.get_solver(self.solver_id)
        if before is None or str(before.status) == status:
            return
        updated = self.repositories.solvers.update_solver_status(self.solver_id, status)
        self.repositories.events.append_agent_event(
            self.task.id,
            "SOLVER_STATUS_CHANGED",
            {"from": str(before.status), "status": str(updated.status)},
            solver_id=self.solver_id,
        )


__all__ = ["SolverOutcome", "SolverRunner"]
