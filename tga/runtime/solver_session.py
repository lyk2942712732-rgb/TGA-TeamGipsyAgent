"""Deterministic workspace location for the active model session."""

from __future__ import annotations

from pathlib import Path

from tga.infrastructure.workspace import SolverWorkspaceService


class SolverSessionState:
    """Derive workspace paths without writing duplicate recovery projections."""

    def __init__(
        self,
        *,
        run_root: str | Path,
        task_id: str,
        solver_id: str,
        solver_run_id: str | None = None,
    ):
        root = Path(run_root) / task_id / "solvers" / solver_id
        self.root = root
        if solver_run_id:
            self.workspace = (
                Path(run_root) / task_id / "workspace" / "solver-runs" / solver_run_id
            )
            self.scratch = self.workspace / "scratch"
            self.outputs = self.workspace / "outputs"
            self.scratch.mkdir(parents=True, exist_ok=True)
            self.outputs.mkdir(parents=True, exist_ok=True)
        else:
            layout = SolverWorkspaceService(Path(run_root) / task_id).for_solver(
                solver_id
            )
            self.workspace = layout.root
            self.scratch = layout.scratch
            self.outputs = layout.outputs
