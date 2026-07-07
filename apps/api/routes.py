"""HTTP routes for TGA's own UI."""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.schemas import CreateTaskRequest, CreateTaskResponse, HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.post("/tasks", response_model=CreateTaskResponse)
def create_task(payload: CreateTaskRequest) -> CreateTaskResponse:
    # Week 1 API contract endpoint. The run loop will be wired here after the
    # core scheduler and UI agree on task lifecycle states.
    return CreateTaskResponse(task_id=payload.task.id)

