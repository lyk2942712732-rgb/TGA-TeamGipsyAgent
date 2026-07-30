"""Reports HTTP boundaries."""

from __future__ import annotations


from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from apps.api.routes.support import _application_commands, _runtime_queries, _snapshot

router = APIRouter(tags=["reports"])


@router.get("/tasks/{task_id}/report")
def task_report(task_id: str) -> Response:
    """Render a report without changing the task directory or event stream."""
    _snapshot(task_id)
    text = _runtime_queries().report(task_id)
    return Response(text, media_type="text/markdown; charset=utf-8")


@router.post("/tasks/{task_id}/report/export")
def export_task_report(task_id: str) -> FileResponse:
    """Explicit, audited persistence operation for a Markdown export."""
    _snapshot(task_id)
    report_path = _application_commands().export_report(task_id)
    return FileResponse(report_path, media_type="text/markdown", filename=report_path.name)
