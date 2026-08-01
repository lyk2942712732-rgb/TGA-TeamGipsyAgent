"""Tasks HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from tga.application.projections.models import TaskDetailResponse

from tga.modes import mode_profiles_payload, normalize_mode
from tga.runtime.task_creation import (
    CreateTaskCommand,
    TaskCreationError,
    available_capabilities,
    build_mcp_capability_snapshot,
)
from tga.runtime.service import UnsupportedTaskSchemaError
from tga.skills.context import SkillContextAssembler
from tga.skills.selection import SkillSelectionRequest, SkillSelector

from apps.api.routes.support import CreateTaskRequest, SkillPreviewRequest, TaskRuntimeService, _application_commands, _catalog_runner, _runtime_queries, _schedule_runtime_runner, _task_root
from apps.api.routes import support

router = APIRouter(tags=["tasks"])


@router.get("/mode-profiles")
def mode_profiles() -> dict[str, Any]:
    return {"schema_version": 3, "profiles": mode_profiles_payload()}


@router.post("/tasks")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    """Translate an HTTP request into one task-creation command."""
    try:
        result = _application_commands().create_task(CreateTaskCommand(
            task_id=payload.id,
            name=payload.name,
            workspace_id=payload.workspace_id,
            mode=payload.mode,
            goal=payload.goal,
            mode_options=payload.mode_options,
            input_text=payload.input.text,
            file_ids=payload.input.file_ids,
            execution_policy=payload.execution_policy,
            selected_skill_names=tuple(payload.selected_skills) if payload.selected_skills is not None else None,
            preflight_fingerprint=payload.preflight_fingerprint,
        ), mcp_manager=_catalog_runner(), schedule=_schedule_runtime_runner)
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


@router.post("/tasks/preflight")
def preflight_task(payload: CreateTaskRequest) -> dict[str, Any]:
    try:
        result = _application_commands().preflight_task(CreateTaskCommand(
            task_id=payload.id,
            name=payload.name,
            workspace_id=payload.workspace_id,
            mode=payload.mode,
            goal=payload.goal,
            mode_options=payload.mode_options,
            input_text=payload.input.text,
            file_ids=payload.input.file_ids,
            execution_policy=payload.execution_policy,
            selected_skill_names=tuple(payload.selected_skills) if payload.selected_skills is not None else None,
        ), mcp_manager=_catalog_runner())
    except TaskCreationError as exc:
        status = 409 if exc.code in {
            "MODEL_NOT_CONFIGURED", "MODEL_NOT_VERIFIED", "SESSION_EXISTS"
        } else 422
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return {
        "fingerprint": result.fingerprint,
        "task_id": result.task.id,
        "checks": list(result.checks),
        "skill_snapshot": {
            "selector": result.task_common_skills.selector,
            "count": len(result.task_common_skills.skills),
            "content_sha256": result.skill_fingerprint,
        },
        "mcp_catalog_version": result.task.mcp_capabilities.catalog_version,
        "model_verification_id": (
            result.task.model_snapshot.verification_id
            if result.task.model_snapshot is not None
            else ""
        ),
    }


@router.post("/tasks/skill-preview")
def preview_task_skills(payload: SkillPreviewRequest) -> dict[str, Any]:
    """Preview the same deterministic Skill selection used at task creation."""
    try:
        mode = normalize_mode(payload.mode)
        capabilities = build_mcp_capability_snapshot(_catalog_runner())
        bundle = SkillSelector().select(SkillSelectionRequest(
            mode=mode,
            goal=payload.goal.strip(),
            prompt=payload.prompt.strip(),
            file_names=tuple(payload.file_names),
            mode_config={**payload.mode_options, "mode": mode},
            available_capabilities=available_capabilities(mode, capabilities, payload.execution_policy),
            selected_skill_names=tuple(payload.selected_skills) if payload.selected_skills is not None else None,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "SKILL_PREVIEW_INVALID", "message": str(exc)}) from exc
    return {
        "selector": bundle.selector,
        "fingerprint": bundle.fingerprint,
        "count": len(bundle.skills),
        "skills": SkillContextAssembler().manifest(bundle),
    }


@router.get("/tasks")
def list_tasks(
    query: str = Query(default="", max_length=255),
    mode: str | None = None,
    status: str | None = None,
    needs_attention: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    return _runtime_queries().tasks(
        query=query, mode=mode, status=status,
        needs_attention=needs_attention, offset=offset, limit=limit,
    )


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task_detail(task_id: str) -> TaskDetailResponse:
    try:
        return _runtime_queries().task_detail(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
            "schema_version": exc.schema_version,
            "required_schema_version": 6,
        }) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    _task_root(task_id)
    if support._runtime_scheduler().is_running(task_id):
        raise HTTPException(status_code=409, detail="running session cannot be deleted")
    try:
        _application_commands().delete_task(task_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task_id, "deleted": True}
