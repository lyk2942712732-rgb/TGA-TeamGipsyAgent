"""Immutable execution authority carried by one durable SolverRun attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tga.runtime.scheduling.concurrency import CancellationToken


@dataclass(frozen=True)
class SolverExecutionContext:
    """Fenced authority for side effects from one SolverRun generation."""

    run_id: str
    task_id: str
    solver_id: str
    owner_id: str
    fencing_token: int
    cancellation: CancellationToken
    _is_valid: Callable[[], bool]

    def is_active(self) -> bool:
        return not self.cancellation.cancelled and self._is_valid()

    def assert_active(self) -> None:
        self.cancellation.raise_if_cancelled()
        if not self._is_valid():
            self.cancellation.cancel("solver_run_execution_authority_lost")
            self.cancellation.raise_if_cancelled()


__all__ = ["SolverExecutionContext"]
