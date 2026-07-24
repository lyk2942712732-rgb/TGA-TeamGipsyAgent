"""Deterministic workspace location for the active model session."""

from __future__ import annotations

from pathlib import Path
class SolverSessionState:
    """Derive workspace paths without writing duplicate recovery projections."""

    def __init__(self, *, run_root: str | Path, task_id: str, solver_id: str):
        root = Path(run_root) / task_id / "solvers" / solver_id
        self.root = root
        # Schema-v4 Sessions use one durable workspace across every execution
        # subject and local MCP container. Per-Solver state remains isolated.
        shared = Path(run_root) / task_id / "workspace"
        self.workspace = shared if shared.exists() else root / "workspace"
