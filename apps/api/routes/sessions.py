"""Sessions HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from tga.application.commands import (
    ApprovalDecisionRequest,
    IntentRetryRequest,
    InterventionRequest,
    SolverControlRequest,
)
from tga.application.projections.models import (
    ApprovalPage,
    EvidencePageResponse,
    IntentPage,
    RuntimeSnapshotResponse,
    SolverResponse,
    TeamResponse,
)
from tga.models.bootstrap import model_config_status
from tga.runtime.coordinator import SessionTransitionError
from tga.runtime.service import UnsupportedTaskSchemaError

from apps.api.routes.support import (
    ControlRequest,
    StartRequest,
    _application_commands,
    _runtime_queries,
    _schedule_runtime_runner,
    _snapshot,
)

router = APIRouter(tags=["sessions"])


@router.get(
    "/tasks/{task_id}/session",
    response_model=RuntimeSnapshotResponse,
)
def get_session(task_id: str) -> dict[str, Any]:
    return _snapshot(task_id)


@router.get("/tasks/{task_id}/team", response_model=TeamResponse)
def get_team(task_id: str):
    return _query(lambda: _runtime_queries().team(task_id))


@router.get("/tasks/{task_id}/solvers/{solver_id}", response_model=SolverResponse)
def get_solver(task_id: str, solver_id: str):
    return _query(lambda: _runtime_queries().solver(task_id, solver_id))


@router.get("/tasks/{task_id}/intents", response_model=IntentPage)
def get_intents(
    task_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return _query(
        lambda: _runtime_queries().intents(task_id, offset=offset, limit=limit)
    )


@router.get("/tasks/{task_id}/evidence", response_model=EvidencePageResponse)
def get_evidence(
    task_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return _query(
        lambda: _runtime_queries().evidence(task_id, offset=offset, limit=limit)
    )


@router.get("/tasks/{task_id}/approvals", response_model=ApprovalPage)
def get_approvals(
    task_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected|expired|cancelled)$"),
):
    return _query(
        lambda: _runtime_queries().approvals(
            task_id, offset=offset, limit=limit, status=status
        )
    )


@router.post("/tasks/{task_id}/start")
def start_session(task_id: str, payload: StartRequest) -> dict[str, Any]:
    """Resume initialization for a v2 session after a process restart."""
    _snapshot(task_id)
    model_status = model_config_status()
    if not model_status["configured"]:
        raise HTTPException(status_code=409, detail="model_not_configured")
    if model_status.get("verification_status") != "verified":
        raise HTTPException(status_code=409, detail="model_not_verified")
    result = _command(lambda: _application_commands().start_task(
        task_id, initial_hint=payload.initial_hint
    ))
    if result.get("accepted"):
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/control")
def control(task_id: str, payload: ControlRequest) -> dict[str, Any]:
    _snapshot(task_id)
    if payload.action == "resume":
        model_status = model_config_status()
        if not model_status["configured"]:
            raise HTTPException(status_code=409, detail="model_not_configured")
        if model_status.get("verification_status") != "verified":
            raise HTTPException(status_code=409, detail="model_not_verified")
    result = _command(lambda: _application_commands().task_control(
        task_id, **payload.model_dump(exclude_none=True)
    ))
    if result.get("accepted") and payload.action in {"resume", "approve_action", "reject_action"}:
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/interventions")
def add_intervention(task_id: str, payload: InterventionRequest) -> dict[str, Any]:
    return _command(
        lambda: _application_commands().intervention(task_id, payload)
    )


@router.post("/tasks/{task_id}/approvals/{action_id}/decision")
def decide_approval(
    task_id: str, action_id: str, payload: ApprovalDecisionRequest
) -> dict[str, Any]:
    result = _command(
        lambda: _application_commands().approval_decision(
            task_id, action_id, payload
        )
    )
    if result.get("accepted"):
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/solvers/{solver_id}/control")
def control_solver(
    task_id: str, solver_id: str, payload: SolverControlRequest
) -> dict[str, Any]:
    result = _command(
        lambda: _application_commands().solver_control(task_id, solver_id, payload)
    )
    if result.get("accepted") and payload.action == "resume":
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/intents/{intent_id}/retry")
def retry_intent(
    task_id: str, intent_id: str, payload: IntentRetryRequest
) -> dict[str, Any]:
    result = _command(
        lambda: _application_commands().retry_intent(task_id, intent_id, payload)
    )
    if result.get("accepted"):
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


def _query(call):
    try:
        return call()
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
            "schema_version": exc.schema_version,
            "required_schema_version": 6,
        }) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _command(call) -> dict[str, Any]:
    try:
        result = call()
        return result if isinstance(result, dict) else result.model_dump(mode="json")
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
            "schema_version": exc.schema_version,
            "required_schema_version": 6,
        }) from exc
    except SessionTransitionError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "from_status": exc.from_status,
            "to_status": exc.to_status,
            "message": str(exc),
        }) from exc
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
