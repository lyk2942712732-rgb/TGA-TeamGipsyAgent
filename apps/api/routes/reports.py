"""Reports HTTP boundaries."""

from __future__ import annotations


from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from tga.runtime.service import TaskRuntimeService

from apps.api.routes.support import TaskRuntimeService, _run_root, _snapshot

router = APIRouter(tags=["reports"])


@router.get("/tasks/{task_id}/report")
def task_report(task_id: str) -> Response:
    """Render a report without changing the task directory or event stream."""
    _snapshot(task_id)
    text = TaskRuntimeService(run_root=_run_root()).render_report(task_id)
    return Response(text, media_type="text/markdown; charset=utf-8")


@router.post("/tasks/{task_id}/report/export")
def export_task_report(task_id: str) -> FileResponse:
    """Explicit, audited persistence operation for a Markdown export."""
    _snapshot(task_id)
    report_path = TaskRuntimeService(run_root=_run_root()).write_report(task_id)
    return FileResponse(report_path, media_type="text/markdown", filename=report_path.name)
