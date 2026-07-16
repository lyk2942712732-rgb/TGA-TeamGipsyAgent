"""Durable private state owned by one Solver execution subject."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tga.contracts import SolverRecord
from tga.runtime.session import _atomic_write_json


class SolverSessionState:
    """Persist bounded Solver state without copying raw tool output.

    The SQLite event/evidence stores remain authoritative. This file is a
    recoverable per-Solver startup/checkpoint projection and proves that a
    role has its own workspace and planning state rather than being a UI-only
    label.
    """

    def __init__(self, *, run_root: str | Path, task_id: str, solver_id: str):
        root = Path(run_root) / task_id / "solvers" / solver_id
        self.root = root
        self.workspace = root / "workspace"
        self.path = root / "session" / "state.json"

    def ensure(self, solver: SolverRecord) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.checkpoint(solver=solver, latest_seq=0, action_count=0, context={})

    def checkpoint(
        self, *, solver: SolverRecord, latest_seq: int, action_count: int,
        context: dict[str, Any],
    ) -> None:
        board = context.get("board") or {}
        payload = {
            "schema_version": 2,
            "task_id": solver.task_id,
            "solver": solver.model_dump(mode="json"),
            "workspace": str(self.workspace),
            "latest_seq": latest_seq,
            "action_count": action_count,
            "context": {
                "active_hypothesis_ids": [
                    item.get("id") for item in board.get("hypotheses") or []
                    if item.get("status") in {"pending", "testing", "inconclusive"}
                ][:8],
                "memory_ids": [item.get("id") for item in (board.get("memory") or [])[-12:]],
                "artifact_ids": [item.get("id") for item in (context.get("artifacts") or [])[-12:]],
            },
        }
        _atomic_write_json(self.path, payload)

