"""Application command facade over the runtime command backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tga.application.commands.models import (
    ApprovalDecisionRequest,
    IntentRetryRequest,
    InterventionRequest,
    SolverControlRequest,
)
from tga.runtime.service import TaskRuntimeService
from tga.runtime.manager import Manager
from tga.runtime.task_creation import CreateTaskCommand, TaskCreationService


class RuntimeCommands:
    def __init__(
        self, *, run_root: str | Path, manager: Any | None = None,
        service_type=TaskRuntimeService,
    ) -> None:
        self.backend = service_type(
            run_root=run_root,
            manager=manager or Manager(run_root=run_root),
        )

    def task_control(self, task_id: str, *, action: str, action_id: str | None = None):
        return self.backend.command(
            "control_session", task_id, action=action, action_id=action_id
        )

    def start_task(self, task_id: str, *, initial_hint: str | None = None):
        return self.backend.command(
            "start_session", task_id, initial_hint=initial_hint
        )

    def create_task(self, command: CreateTaskCommand, *, mcp_manager, schedule):
        return TaskCreationService(
            run_root=self.backend.run_root,
            mcp_manager=mcp_manager,
            schedule=schedule,
        ).create(command)

    def delete_task(self, task_id: str) -> None:
        self.backend.delete_task(task_id)

    def export_report(self, task_id: str):
        return self.backend.write_report(task_id)

    def intervention(self, task_id: str, request: InterventionRequest):
        return self.backend.command(
            "record_intervention", task_id, **request.model_dump()
        )

    def approval_decision(
        self, task_id: str, action_id: str, request: ApprovalDecisionRequest
    ):
        return self.backend.command(
            "control_session",
            task_id,
            action=f"{request.decision}_action",
            action_id=action_id,
            decision_reason=request.reason,
        )

    def solver_control(
        self, task_id: str, solver_id: str, request: SolverControlRequest
    ):
        return self.backend.command(
            "control_solver",
            task_id,
            solver_id=solver_id,
            action=request.action,
            reason=request.reason,
        )

    def retry_intent(
        self, task_id: str, intent_id: str, request: IntentRetryRequest
    ):
        return self.backend.command(
            "retry_intent",
            task_id,
            intent_id=intent_id,
            reason=request.reason,
        )


__all__ = ["RuntimeCommands"]
