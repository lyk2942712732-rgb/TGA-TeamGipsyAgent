"""Inputs HTTP boundaries."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from tga.contracts import SessionFile, TGATask
from tga.inputs import InputLimits, SessionWorkspace, cleanup_expired_staged_inputs, detect_mime_type, media_kind_for, safe_original_name
from tga.runtime.service import UnsupportedTaskSchemaError

from apps.api.routes.support import _api_error, _run_root, _runtime_queries, _task_root

router = APIRouter(tags=["inputs"])


@router.post("/input-uploads", status_code=201)
async def stage_input_upload(
    request: Request,
    filename: str,
) -> dict[str, Any]:
    """Stream one untrusted asset to staging without trusting client MIME data."""

    try:
        original_name = safe_original_name(filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_FILENAME", str(exc), field="filename")) from exc
    limits = InputLimits.from_environment()
    staging_root = (_run_root().resolve() / "_input_staging").resolve()
    cleanup_expired_staged_inputs(staging_root)
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_CONTENT_LENGTH", "invalid content-length")) from exc
    if content_length > limits.max_file_bytes:
        raise HTTPException(status_code=413, detail=_api_error(
            "FILE_TOO_LARGE", "input exceeds per-file size limit", field="file", limit=limits.max_file_bytes,
        ))
    token = uuid4().hex
    asset_id = f"asset_{token}"
    stage = (staging_root / token).resolve()
    try:
        stage.relative_to(staging_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_UPLOAD_TOKEN", "invalid upload token")) from exc
    stage.mkdir(parents=True, exist_ok=False)
    try:
        digest = hashlib.sha256()
        size = 0
        with (stage / "source").open("xb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limits.max_file_bytes:
                    raise HTTPException(status_code=413, detail=_api_error(
                        "FILE_TOO_LARGE", "input exceeds per-file size limit", field="file", limit=limits.max_file_bytes,
                    ))
                digest.update(chunk)
                handle.write(chunk)
        metadata = {
            "token": token,
            "asset_id": asset_id,
            "original_name": original_name,
            "client_mime_type": (request.headers.get("content-type") or "application/octet-stream").split(";", 1)[0],
            "size": size,
            "sha256": digest.hexdigest(),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        metadata["detected_mime_type"] = detect_mime_type(stage / "source", original_name)
        metadata["media_kind"] = media_kind_for(metadata["detected_mime_type"], original_name)
        (stage / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    except Exception:
        for item in stage.glob("*"):
            item.unlink(missing_ok=True)
        stage.rmdir()
        raise
    return {
        "asset": {
            "id": asset_id,
            "originalName": original_name,
            "mimeType": metadata["detected_mime_type"],
            "mediaKind": metadata["media_kind"],
            "size": size,
            "sha256": metadata["sha256"],
            "status": "uploaded",
        }
    }


@router.delete("/input-uploads/{asset_id}")
def delete_input_upload(asset_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"asset_[a-f0-9]{32}", asset_id):
        raise HTTPException(status_code=400, detail=_api_error("INVALID_ASSET_ID", "invalid asset id"))
    staging_root = (_run_root().resolve() / "_input_staging").resolve()
    stage = (staging_root / asset_id.removeprefix("asset_")).resolve()
    try:
        stage.relative_to(staging_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_ASSET_ID", "invalid asset id")) from exc
    if not stage.is_dir():
        raise HTTPException(status_code=404, detail=_api_error("ASSET_NOT_FOUND", "staged asset not found"))
    shutil.rmtree(stage)
    return {"asset_id": asset_id, "deleted": True}


@router.get("/tasks/{task_id}/inputs")
def list_task_inputs(task_id: str) -> dict[str, Any]:
    task = _task(task_id)
    return task.input_manifest()


@router.get("/tasks/{task_id}/inputs/{input_id}")
def get_task_input(task_id: str, input_id: str) -> dict[str, Any]:
    task = _task(task_id)
    return _session_file(task, input_id).manifest_item()


@router.get("/tasks/{task_id}/inputs/{input_id}/read")
def read_task_input(task_id: str, input_id: str, offset: int = 0, limit: int = 16_384) -> dict[str, Any]:
    task = _task(task_id)
    try:
        return SessionWorkspace(_task_root(task_id)).read(_session_file(task, input_id), offset=offset, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="input not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/inputs/{input_id}/search")
def search_task_input(task_id: str, input_id: str, query: str, limit: int = 20) -> dict[str, Any]:
    task = _task(task_id)
    try:
        return SessionWorkspace(_task_root(task_id)).search(_session_file(task, input_id), query=query, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="input not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _session_file(task: TGATask, input_id: str) -> SessionFile:
    item = next(
        (candidate for candidate in task.session_input.files if candidate.id == input_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=_api_error("INPUT_NOT_FOUND", "input not found"))
    return item


def _task(task_id: str) -> TGATask:
    try:
        return TGATask.model_validate(_runtime_queries().task_definition(task_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
            "schema_version": exc.schema_version,
            "required_schema_version": 6,
        }) from exc
