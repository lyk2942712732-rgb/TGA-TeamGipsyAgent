"""Reusable solver definitions and durable task-local instances."""

from tga.domain.solver.assignments import SolverAssignment
from tga.domain.solver.budgets import SolverBudget, SolverBudgetUsage
from tga.domain.solver.definitions import SolverDefinition, SolverOutputContract
from tga.domain.solver.instances import SolverInstance, SolverTimestamps, ToolPolicySnapshot
from tga.domain.solver.results import (
    ReportResult,
    ReviewResult,
    SolverError,
    WorkerCoverage,
    WorkerResult,
)
from tga.domain.solver.status import SolverInstanceStatus, WorkerResultStatus
from tga.domain.solver.team_runtime import TaskOrchestratorStatus, TeamRuntimeState
from tga.domain.solver.leases import SolverLease, TaskOrchestratorLease

__all__ = [
    "SolverAssignment", "SolverBudget", "SolverBudgetUsage",
    "ReportResult", "ReviewResult", "SolverDefinition", "SolverError", "SolverInstance", "SolverInstanceStatus",
    "SolverOutputContract", "SolverTimestamps", "ToolPolicySnapshot", "WorkerCoverage",
    "SolverLease", "TaskOrchestratorLease", "TaskOrchestratorStatus",
    "TeamRuntimeState", "WorkerResult", "WorkerResultStatus",
]
