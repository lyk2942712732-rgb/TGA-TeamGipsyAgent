"""Tasks HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from tga.modes import mode_profiles_payload
from tga.runtime.task_creation import CreateTaskCommand, TaskCreationError, TaskCreationService

from apps.api.routes.support import CreateTaskRequest, TaskRuntimeService, _catalog_runner, _run_root, _schedule_runtime_runner, _task_root
from apps.api.routes import support

router = APIRouter(tags=["tasks"])


@router.get("/mode-profiles")
def mode_profiles() -> dict[str, Any]:
    return {"schema_version": 3, "profiles": mode_profiles_payload()}


@router.post("/tasks")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    """Translate an HTTP request into one task-creation command."""
    try:
        result = TaskCreationService(
            run_root=_run_root(),
            mcp_manager=_catalog_runner(),
            schedule=_schedule_runtime_runner,
        ).create(CreateTaskCommand(
            task_id=payload.id,
            name=payload.name,
            mode=payload.mode,
            goal=payload.goal,
            mode_options=payload.mode_options,
            input_text=payload.input.text,
            file_ids=payload.input.file_ids,
            execution_policy=payload.execution_policy,
        ))
    except TaskCreationError as exc:
        status = 409 if exc.code in {"MODEL_NOT_CONFIGURED", "MODEL_NOT_VERIFIED", "SESSION_EXISTS", "SESSION_START_REJECTED"} else 422
        detail: Any = {"code": exc.code, "message": str(exc)}
        raise HTTPException(status_code=status, detail=detail) from exc
    return {
        "task_id": result.task_id,
        "status": result.status,
        "scheduled": result.scheduled,
        "mcp_capabilities": result.mcp_capabilities.model_dump(mode="json"),
    }


@router.get("/tasks")
def list_tasks() -> dict[str, list[dict[str, Any]]]:
    return {"tasks": TaskRuntimeService(run_root=_run_root()).list_tasks()}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    _task_root(task_id)
    if support._runtime_scheduler().is_running(task_id):
        raise HTTPException(status_code=409, detail="running session cannot be deleted")
    try:
        TaskRuntimeService(run_root=_run_root()).delete_task(task_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task_id, "deleted": True}
