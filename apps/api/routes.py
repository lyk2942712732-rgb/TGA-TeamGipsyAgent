"""HTTP routes for TGA's own UI."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from apps.api.schemas import CreateTaskRequest, CreateTaskResponse, HealthResponse, TaskSnapshotResponse
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.orchestrator.run_loop import run_task
from tga.reporting.markdown_report import render_markdown_report
from tga.tools.bootstrap import build_tool_runner_from_env
from tga.workers.subprocess_worker import SubprocessWorker

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.post("/tasks", response_model=CreateTaskResponse)
def create_task(payload: CreateTaskRequest) -> CreateTaskResponse:
    task = payload.task
    run_root = _run_root()
    task_root = run_root / task.id
    artifact_store = ArtifactStore(task_root / "artifacts")
    store = EvidenceStore(task_root / "evidence.db")
    worker = SubprocessWorker(
        artifact_store=artifact_store,
        tool_runner=build_tool_runner_from_env(artifact_store),
    )
    run_task(task=task, store=store, worker=worker, run_root=str(run_root))
    report = render_markdown_report(store.task_snapshot(task.id))
    report_path = task_root / "reports" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return CreateTaskResponse(
        task_id=task.id,
        report_path=str(report_path),
        run_root=str(run_root),
    )


@router.get("/tasks/{task_id}", response_model=TaskSnapshotResponse)
def task_snapshot(task_id: str) -> TaskSnapshotResponse:
    store = EvidenceStore(_task_root(task_id) / "evidence.db")
    return TaskSnapshotResponse(task_id=task_id, snapshot=store.task_snapshot(task_id))


@router.get("/tasks/{task_id}/report")
def task_report(task_id: str) -> FileResponse:
    return FileResponse(_task_root(task_id) / "reports" / "report.md", media_type="text/markdown")


def _run_root() -> Path:
    return Path(os.environ.get("TGA_RUN_ROOT", "runs")).resolve()


def _task_root(task_id: str) -> Path:
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError("invalid task id")
    return _run_root() / task_id
