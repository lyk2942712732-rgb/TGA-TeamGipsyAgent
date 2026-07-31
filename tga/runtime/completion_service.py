"""Single application boundary for accepting task completion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.orchestration import TaskOrchestrator


class TaskCompletionService:
    """Validate and commit a completion as one task aggregate transition."""

    def __init__(self, *, task, store):
        self.task = task
        self.store = store
        self.repositories = PersistenceBundle(store)
        self.orchestrator = TaskOrchestrator(
            task=task,
            repositories=self.repositories,
        )

    def complete(
        self,
        *,
        solver_id: str,
        proposal: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        finalize_validated: Callable[[dict[str, Any]], None] | None = None,
        session_completion: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        def validate_and_finalize(value: dict[str, Any]) -> dict[str, Any]:
            result = validate(value)
            if result.get("accepted") and finalize_validated is not None:
                finalize_validated(result)
            if result.get("accepted") and session_completion is not None:
                session_completion(result)
            return result

        result = self.orchestrator.complete_task(
            solver_id=solver_id,
            proposal=proposal,
            validator=validate_and_finalize,
        )
        return result


__all__ = ["TaskCompletionService"]
