"""Sessions HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from tga.models.bootstrap import model_config_status

from apps.api.routes.support import ControlRequest, HintRequest, StartRequest, _runtime_command, _schedule_runtime_runner, _snapshot

router = APIRouter(tags=["sessions"])


@router.get("/tasks/{task_id}/session")
def get_session(task_id: str) -> dict[str, Any]:
    return _snapshot(task_id)


@router.post("/tasks/{task_id}/start")
def start_session(task_id: str, payload: StartRequest) -> dict[str, Any]:
    """Resume initialization for a v2 session after a process restart."""
    _snapshot(task_id)
    model_status = model_config_status()
    if not model_status["configured"]:
        raise HTTPException(status_code=409, detail="model_not_configured")
    if model_status.get("verification_status") != "verified":
        raise HTTPException(status_code=409, detail="model_not_verified")
    result = _runtime_command("start_session", task_id, payload.model_dump(exclude_none=True))
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
    result = _runtime_command("control_session", task_id, payload.model_dump(exclude_none=True))
    if result.get("accepted") and payload.action in {"resume", "approve_action", "reject_action"}:
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/hints")
def add_hint(task_id: str, payload: HintRequest) -> dict[str, Any]:
    _snapshot(task_id)
    return _runtime_command("add_hint", task_id, {"content": payload.content})
