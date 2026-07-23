"""The sole public API for the durable TGA v2 runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from tga.capabilities.mcp import health_snapshot, tool_catalog_snapshot
from tga.capabilities.registry import build_default_registry
from tga.contracts import (
    ExecutionPolicy,
    MCPCapabilitySnapshot,
    MCPCapabilityTool,
    SessionFile,
    TGATask,
)
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.evidence.store import EvidenceStore
from tga.inputs import (
    InputLimits,
    SessionWorkspace,
    TaskInputStore,
    cleanup_expired_staged_inputs,
    detect_mime_type,
    media_kind_for,
    resource_by_id,
    safe_original_name,
    task_artifact_root,
)
from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env, model_config_status
from tga.runtime.service import TaskRuntimeService
from tga.runtime.prompts import ROLE_INSTRUCTIONS
from tga.modes import is_task_mode, mode_profile, mode_profiles_payload, normalize_mode, validate_task_profile
from tga.skills.registry import SkillRegistry
from tga.skills.loader import load_skill_text
from tga.skills.store import MAX_SKILL_BYTES, SkillStore
from tga.tools.mcp_manager import MCPManager
from tga.tools.mcp_policy import redact_sensitive
from tga.tools.mcp_importer import DEFAULT_MAX_PACKAGE_BYTES, MCPImageImporter, MCPImportError
from tga.tools.mcp_config import (
    MCPServerConfig,
    delete_mcp_server,
    load_mcp_config,
    patch_mcp_server,
    set_mcp_server_enabled,
    upsert_mcp_server,
)


router = APIRouter(prefix="/v2", tags=["runtime-v2"])


def _api_error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


class _CatalogOnlyArtifactStore:
    """Sentinel passed to a catalog-only ToolRunner.

    The v2 capability and health endpoints must never execute a tool or write
    an artifact.  ``ToolRunner`` only uses its artifact store during
    ``run_tool``; this sentinel makes an accidental invocation fail loudly
    while still letting B's read-only catalog/health helpers inspect the
    configured runner.
    """

    def save_text(self, **_: Any) -> Any:
        raise RuntimeError("catalog-only tool runner cannot persist artifacts")


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "cancel", "approve_action"]
    action_id: str | None = None


class HintRequest(BaseModel):
    content: str = Field(min_length=1, max_length=800)


class StartRequest(BaseModel):
    initial_hint: str | None = Field(default=None, max_length=800)


class CreateSessionInputRequest(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    task_file_ids: list[str] = Field(default_factory=list, alias="taskFileIds", max_length=64)
    hint_text: str | None = Field(default=None, alias="hintText", max_length=16_384)
    hint_file_ids: list[str] = Field(default_factory=list, alias="hintFileIds", max_length=64)


class CreateTaskRequest(BaseModel):
    """Schema-v4 product request. Legacy fields are accepted only to warn."""

    model_config = {"extra": "allow", "populate_by_name": True}

    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=255)
    mode: str
    goal: str | None = Field(default=None, max_length=8000)
    mode_options: dict[str, Any] = Field(default_factory=dict, alias="modeOptions")
    input: CreateSessionInputRequest
    execution_policy: ExecutionPolicy = Field(alias="executionPolicy")


class LLMSettingsRequest(BaseModel):
    base_url: str
    api_key: str
    model: str


class MCPEnabledRequest(BaseModel):
    enabled: bool


class MCPMethodTestRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirm_active: bool = False


class SkillUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    modes: list[str] = Field(min_length=1, max_length=5)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=32)
    version: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=500_000)


_runner_lock = threading.Lock()
_running_tasks: set[str] = set()


def _run_root() -> Path:
    return Path(os.environ.get("TGA_RUN_ROOT", "runs"))


def _task_root(task_id: str) -> Path:
    try:
        return TaskRuntimeService(run_root=_run_root()).task_root(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid task id") from exc


def _snapshot(task_id: str) -> dict[str, Any]:
    try:
        snapshot = TaskRuntimeService(run_root=_run_root()).snapshot(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return _normalize_snapshot(snapshot)


def _normalize_event(event: dict[str, Any], fallback_seq: int) -> dict[str, Any]:
    normalized = dict(event)
    normalized["seq"] = int(normalized.get("seq") or normalized.get("id") or fallback_seq)
    # Optional fields are omitted from the public envelope so a malformed
    # payload never prevents the whole Runtime page from loading.
    payload = normalized.get("payload")
    normalized["payload"] = _compact_public_payload(payload if isinstance(payload, dict) else {})
    return normalized


def _compact_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_public_payload(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_compact_public_payload(item) for item in value]
    return value


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project the durable v2 repository into its public UI contract."""
    events = _runtime_events(snapshot)
    latest_seq = max((event["seq"] for event in events), default=0)
    session = snapshot["session"]
    board = snapshot.get("board") or {}
    agent_runtime = any(
        event.get("type") == "SESSION_STARTED" and (event.get("payload") or {}).get("runtime") == "agent_session"
        for event in events
    )
    solvers = snapshot.get("solvers") or []
    if agent_runtime:
        solvers = [item for item in solvers if item.get("role") == "main"]
    http_sessions: dict[str, dict[str, Any]] = {}
    observer_directives: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "HTTP_SESSION_STATUS":
            http_sessions[str(event.get("solver_id") or "main")] = event.get("payload") or {}
        elif event.get("type") == "OBSERVER_DIRECTIVE":
            observer_directives.append({
                "seq": event.get("seq"),
                "created_at": event.get("created_at"),
                **(event.get("payload") or {}),
            })
    result = {
        "task": snapshot.get("task") or {},
        "session": {
            "status": session["status"],
            "turn_count": int(session["turn_count"]),
            "max_turns": int(session["max_turns"]),
            "active_solver_id": session.get("active_solver_id"),
            "stop_reason": session.get("stop_reason"),
        },
        "solvers": solvers,
        "challenge": snapshot.get("challenge") or {},
        "subagents": [] if agent_runtime else snapshot.get("subagents") or [],
        "board": {
            "hypotheses": [] if agent_runtime else board.get("hypotheses") or snapshot.get("hypotheses") or [],
            "memory": board.get("memory") or snapshot.get("memory") or snapshot.get("memory_entries") or [],
            "strategy_cards": board.get("strategy_cards") or snapshot.get("strategy_cards") or [],
        },
        "actions": [_normalize_action(item) for item in (snapshot.get("actions") or [])],
        "flags": snapshot.get("flags") or [],
        "findings": snapshot.get("findings") or [],
        "artifacts": snapshot.get("artifacts") or [],
        "artifact_indexes": [
            {
                "artifact_id": item.get("artifact_id"),
                "document_type": item.get("document_type"),
                "extraction_status": item.get("extraction_status"),
                "summary": item.get("summary"),
                "segment_count": len(item.get("segments") or []),
                "source_refs": [segment.get("ref") for segment in (item.get("segments") or [])[:16]],
            }
            for item in (snapshot.get("artifact_indexes") or [])
        ],
        "http_sessions": list(http_sessions.values()),
        "observer": {"directives": observer_directives[-20:]},
        "context_metrics": (snapshot.get("context_metrics") or [])[-100:],
        "events": events,
        "latest_seq": latest_seq,
        "schema_version": 2,
    }
    return result


def _normalize_action(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten the persisted tool result into the Web action projection."""
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    summary = result.get("summary") or item.get("summary") or ""
    return {
        **{key: value for key, value in item.items() if key not in {"arguments_json", "result"}},
        "arguments": _public_action_arguments(item.get("arguments") or {}),
        # A running action has no result yet. Keep the public contract stable
        # instead of emitting null and making one in-flight tool invalidate the
        # entire Runtime snapshot in strict clients.
        "summary": str(summary),
        "artifact_ids": result.get("artifact_ids") or [],
        "error": result.get("error"),
    }


def _public_action_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Expose routing/governance fields to Web without request bodies or credentials."""
    public: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "body":
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="replace")
            public[key] = {"present": value is not None, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()[:16]}
        elif key == "headers" and isinstance(value, dict):
            public[key] = {
                str(name): "[REDACTED]" if _sensitive_name(str(name)) else str(item)[:200]
                for name, item in value.items()
            }
        elif key == "query" and isinstance(value, dict):
            public[key] = {
                str(name): "[REDACTED]" if _sensitive_name(str(name)) else str(item)[:200]
                for name, item in value.items()
            }
        elif key in {"source", "content", "command", "stdin"}:
            raw = str(value).encode("utf-8", errors="replace")
            public[key] = {"present": bool(raw), "chars": len(raw), "sha256": hashlib.sha256(raw).hexdigest()[:16]}
        else:
            public[key] = value
    return public


def _sensitive_name(name: str) -> bool:
    lowered = name.casefold()
    return any(part in lowered for part in ("authorization", "cookie", "token", "secret", "password", "api-key", "api_key"))


def _runtime_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """AgentEvent is the only cursor source for a v2 session."""
    source_events = snapshot.get("agent_events") or []
    normalized = [
        _normalize_event(event, index + 1)
        for index, event in enumerate(source_events)
    ]
    return sorted(normalized, key=lambda event: event["seq"])
@router.get("/tasks/{task_id}/session")
def get_session(task_id: str) -> dict[str, Any]:
    return _snapshot(task_id)


@router.get("/mode-profiles")
def mode_profiles() -> dict[str, Any]:
    return {"schema_version": 3, "profiles": mode_profiles_payload()}


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


@router.post("/tasks")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    """Claim staged assets and create one schema-v4 Session transaction."""
    if not model_config_status()["configured"]:
        raise HTTPException(status_code=409, detail="model_not_configured")
    try:
        mode = normalize_mode(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_MODE", "message": str(exc)}) from exc
    task_id = payload.id or f"task_{uuid4().hex[:12]}"
    task_root = _task_root(task_id)
    if task_root.exists():
        raise HTTPException(status_code=409, detail={"code": "SESSION_EXISTS", "message": "Session id already exists"})
    deprecated = sorted(
        key for key in (payload.model_extra or {})
        if key in {"task", "initial_hint", "target", "targets", "hints", "targetUrls", "references", "mcpResources", "mcpTools", "mcpServiceGrants", "mcpMethodGrants", "mcp_servers", "mcp_direct_tools"}
    )
    workspace = SessionWorkspace(task_root)
    cleanup_stages: list[Path] = []
    try:
        session_input, cleanup_stages = workspace.claim_staged(
            staging_root=_run_root().resolve() / "_input_staging",
            task_asset_ids=payload.input.task_file_ids,
            hint_text=payload.input.hint_text,
            hint_asset_ids=payload.input.hint_file_ids,
        )
        mode_options = {**payload.mode_options, "mode": mode}
        capabilities = _new_session_mcp_capabilities()
        task = TGATask(
            id=task_id,
            name=payload.name.strip(),
            mode=mode,
            goal=(payload.goal or mode_profile(mode).default_goal).strip(),
            mode_config=mode_options,
            execution_policy=payload.execution_policy,
            session_input=session_input,
            mcp_capabilities=capabilities,
            schema_version=4,
        )
        validate_task_profile(task)
        result = TaskRuntimeService(run_root=_run_root()).create_task(task)
    except (OSError, ValueError) as exc:
        shutil.rmtree(task_root, ignore_errors=True)
        raise HTTPException(status_code=422, detail={"code": "SESSION_CREATE_FAILED", "message": str(exc)}) from exc
    except Exception:
        # Keep staged assets retryable but never retain a half-created Session.
        shutil.rmtree(task_root, ignore_errors=True)
        raise
    if not result.get("accepted"):
        shutil.rmtree(task_root, ignore_errors=True)
        raise HTTPException(status_code=409, detail=str(result.get("reason") or "session did not start"))
    for stage in cleanup_stages:
        shutil.rmtree(stage, ignore_errors=True)
    if deprecated:
        _append_deprecation_audit(task_id, deprecated)
    return {
        "task_id": task.id,
        "status": result["status"],
        "scheduled": _schedule_runtime_runner(task.id),
        "deprecated_fields_ignored": deprecated,
        "mcp_capabilities": task.mcp_capabilities.model_dump(mode="json"),
    }


def _new_session_mcp_capabilities() -> MCPCapabilitySnapshot:
    manager = _catalog_runner()
    snapshot = manager.ensure_catalog()
    enabled = {
        server_id for server_id, server in (manager.config.servers.items() if manager.config else [])
        if server.enabled
    }
    routes = [item for item in snapshot.routes if item.server_id in enabled]
    # An enabled configured server remains visible even when it legitimately
    # exposes zero tools. Explicit unavailable/disabled states are excluded;
    # callable methods still require a discovered route below.
    server_ids = sorted({
        item.server_id
        for item in snapshot.servers
        if item.server_id in enabled
        and (item.status in {"discovered", "reachable"} or item.error is None)
    } | {item.server_id for item in routes})
    return MCPCapabilitySnapshot(
        catalog_version=snapshot.version,
        server_ids=server_ids,
        tools=[MCPCapabilityTool(**item.model_dump(mode="json")) for item in routes],
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _append_deprecation_audit(task_id: str, fields: list[str]) -> None:
    path = _task_root(task_id) / "workspace" / "state" / "deprecations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "fields": fields,
            "behavior": "ignored",
        }, ensure_ascii=False) + "\n")


@router.get("/tasks/{task_id}/inputs")
def list_task_inputs(task_id: str) -> dict[str, Any]:
    task = TGATask.model_validate(_snapshot(task_id)["task"])
    return task.input_manifest()


@router.get("/tasks/{task_id}/inputs/{input_id}")
def get_task_input(task_id: str, input_id: str) -> dict[str, Any]:
    task = TGATask.model_validate(_snapshot(task_id)["task"])
    if task.schema_version >= 4:
        return _session_file(task, input_id).manifest_item()
    try:
        resource = resource_by_id([*task.targets, *task.hints], input_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="input not found") from exc
    return resource.manifest_item()


@router.get("/tasks/{task_id}/inputs/{input_id}/read")
def read_task_input(task_id: str, input_id: str, offset: int = 0, limit: int = 16_384) -> dict[str, Any]:
    task = TGATask.model_validate(_snapshot(task_id)["task"])
    try:
        if task.schema_version >= 4:
            return SessionWorkspace(_task_root(task_id)).read(_session_file(task, input_id), offset=offset, limit=limit)
        resource = resource_by_id([*task.targets, *task.hints], input_id)
        return TaskInputStore(_task_root(task_id)).read(resource, offset=offset, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="input not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/inputs/{input_id}/search")
def search_task_input(task_id: str, input_id: str, query: str, limit: int = 20) -> dict[str, Any]:
    task = TGATask.model_validate(_snapshot(task_id)["task"])
    try:
        if task.schema_version >= 4:
            return SessionWorkspace(_task_root(task_id)).search(_session_file(task, input_id), query=query, limit=limit)
        resource = resource_by_id([*task.targets, *task.hints], input_id)
        return TaskInputStore(_task_root(task_id)).search(resource, query=query, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="input not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _session_file(task: TGATask, input_id: str) -> SessionFile:
    item = next(
        (candidate for candidate in [*task.session_input.task_files, *task.session_input.hint.files] if candidate.id == input_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=_api_error("INPUT_NOT_FOUND", "input not found"))
    return item


@router.get("/tasks")
def list_tasks() -> dict[str, list[dict[str, Any]]]:
    return {"tasks": TaskRuntimeService(run_root=_run_root()).list_tasks()}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    _task_root(task_id)
    with _runner_lock:
        if task_id in _running_tasks:
            raise HTTPException(status_code=409, detail="running session cannot be deleted")
    try:
        TaskRuntimeService(run_root=_run_root()).delete_task(task_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task_id, "deleted": True}


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


@router.get("/settings/llm")
def llm_settings() -> dict[str, Any]:
    status = model_config_status()
    return {
        "configured": bool(status["configured"]),
        "base_url": str(status.get("base_url") or ""),
        "model": str(status.get("model") or ""),
        "api_key_set": bool(os.environ.get("TGA_LLM_API_KEY")),
        "supports_vision": status.get("supports_vision"),
    }


@router.get("/settings/skills")
def skill_settings() -> dict[str, Any]:
    """List packaged and operator-authored skills by compatible scene."""
    return {"schema_version": 3, **SkillRegistry().snapshot()}


@router.get("/settings/skills/{name}")
def skill_detail(name: str) -> dict[str, Any]:
    try:
        detail = SkillRegistry().detail(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_SKILL_NAME", str(exc))) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    return {"skill": detail}


@router.post("/settings/skills/import", status_code=201)
async def import_skill(request: Request) -> dict[str, Any]:
    filename = unquote(request.headers.get("x-tga-filename") or "")
    if not filename.lower().endswith(".md") or Path(filename).name != filename:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", "upload one .md file"))
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_CONTENT_LENGTH", "invalid content-length")) from exc
    if content_length > MAX_SKILL_BYTES:
        raise HTTPException(status_code=413, detail=_api_error("SKILL_TOO_LARGE", "skill file exceeds 512 KB"))
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_SKILL_BYTES:
            raise HTTPException(status_code=413, detail=_api_error("SKILL_TOO_LARGE", "skill file exceeds 512 KB"))
    try:
        text = bytes(body).decode("utf-8")
        candidate = load_skill_text(text, source="upload")
        scene = request.headers.get("x-tga-scene")
        if scene:
            if not is_task_mode(scene):
                raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_SCENE", "unknown skill scene"))
            if scene not in candidate.modes:
                raise HTTPException(
                    status_code=422,
                    detail=_api_error("SKILL_SCENE_MISMATCH", "skill does not declare the selected scene"),
                )
        existing = SkillRegistry().detail(candidate.name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=_api_error("SKILL_EXISTS", "a skill with this name already exists"))
        skill = SkillStore().import_markdown(bytes(body))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", "skill must be UTF-8 Markdown")) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", str(exc))) from exc
    return {"skill": SkillRegistry().detail(skill.name)}


@router.put("/settings/skills/{name}")
def update_skill(name: str, payload: SkillUpdateRequest) -> dict[str, Any]:
    registry = SkillRegistry()
    existing = registry.detail(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    try:
        skill = SkillStore().update(
            name,
            modes=payload.modes,
            capabilities=payload.capabilities,
            tags=payload.tags,
            version=payload.version,
            body=payload.body,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL", str(exc))) from exc
    return {"skill": SkillRegistry().detail(skill.name)}


@router.delete("/settings/skills/{name}")
def delete_skill(name: str) -> dict[str, Any]:
    registry = SkillRegistry()
    existing = registry.detail(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    try:
        store = SkillStore()
        if registry.is_builtin(name):
            store.disable(name)
            deleted = True
        else:
            deleted = store.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_SKILL_NAME", str(exc))) from exc
    return {"name": name, "deleted": deleted}


@router.get("/settings/prompts")
def prompt_settings() -> dict[str, Any]:
    """Describe authoritative role prompts without exposing model secrets."""
    return {
        "schema_version": 2,
        "prompts": [
            {"id": f"solver.{role}", "role": role, "instruction": instruction, "source": "tga.runtime.prompts", "editable": False}
            for role, instruction in ROLE_INSTRUCTIONS.items()
        ],
    }


@router.post("/settings/llm")
def update_llm_settings(payload: LLMSettingsRequest) -> dict[str, Any]:
    os.environ["TGA_LLM_BASE_URL"] = payload.base_url.strip()
    os.environ["TGA_LLM_API_KEY"] = payload.api_key.strip()
    os.environ["TGA_LLM_MODEL"] = payload.model.strip()
    return llm_settings()


@router.post("/settings/llm/verify")
def verify_llm_settings() -> dict[str, Any]:
    """Make an explicit, low-cost action-tool check before a task starts.

    Configuration presence alone cannot detect an invalid model identifier,
    expired key, or an incompatible Function Calling dialect.  This endpoint
    is never called automatically and never returns the key or response body.
    """
    client = build_model_client_from_env()
    if client is None:
        raise HTTPException(status_code=409, detail="model is not configured")
    try:
        response = client.chat_action_tool(
            [
                ModelMessage(role="system", content="You are a protocol connectivity check. Call the supplied tool once."),
                ModelMessage(role="user", content="Confirm the TGA action tool protocol."),
            ],
            tool_name="verify_tga_action_protocol",
            tool_description="Return a harmless protocol verification result.",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            thinking=False,
            temperature=0,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"model verification failed: {str(exc)[:500]}") from exc
    return {"configured": True, "reachable": bool(response.content.strip()), "action_tools": True, "model": getattr(client, "model", "")}


@router.post("/tasks/{task_id}/start")
def start_session(task_id: str, payload: StartRequest) -> dict[str, Any]:
    """Resume initialization for a v2 session after a process restart."""
    if not model_config_status()["configured"]:
        raise HTTPException(status_code=409, detail="model_not_configured")
    _snapshot(task_id)
    result = _runtime_command("start_session", task_id, payload.model_dump(exclude_none=True))
    if result.get("accepted"):
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.get("/tasks/{task_id}/events")
def list_events(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
    snapshot = _snapshot(task_id)
    bounded_limit = max(1, min(limit, 200))
    events = [event for event in snapshot["events"] if event["seq"] > after_seq][:bounded_limit]
    return {"events": events, "latest_seq": snapshot["latest_seq"]}


@router.get("/tasks/{task_id}/events/stream")
async def stream_events(task_id: str, request: Request, after_seq: int = 0) -> StreamingResponse:
    # Resolve before opening the stream so a typo produces a normal 404.
    _snapshot(task_id)
    return StreamingResponse(
        _event_stream(task_id, request, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(task_id: str, request: Request, cursor: int) -> AsyncIterator[str]:
    """Poll the repository so the transport works before the manager owns a bus."""
    heartbeat_at = 0.0
    while not await request.is_disconnected():
        snapshot = _snapshot(task_id)
        events = [event for event in snapshot["events"] if event["seq"] > cursor]
        for event in events:
            cursor = event["seq"]
            yield _sse("event", event)
        now = asyncio.get_running_loop().time()
        if now - heartbeat_at >= 15:
            heartbeat_at = now
            yield _sse("heartbeat", {"latest_seq": snapshot["latest_seq"]})
        await asyncio.sleep(1)


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _catalog_runner() -> MCPManager:
    """Return the product MCP manager backed only by explicit mcp.json."""
    from tga.runtime.manager import get_manager

    return get_manager().mcp_manager


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Expose B's registry verbatim, plus its catalogued MCP methods."""
    snapshot = build_default_registry().snapshot()
    snapshot["tools"] = tool_catalog_snapshot(_catalog_runner())
    return snapshot


@router.get("/tools/health")
def tool_health() -> dict[str, Any]:
    runner = _catalog_runner()
    snapshot = health_snapshot(runner)
    snapshot["configured"] = runner is not None
    return snapshot


@router.post("/tools/mcp/refresh")
def refresh_mcp_catalog() -> dict[str, Any]:
    """Refresh discovery now; active LLM turns retain their prior snapshot."""
    manager = _catalog_runner()
    manager.refresh()
    return manager.status_snapshot()


def _public_server_config(server: MCPServerConfig) -> dict[str, Any]:
    payload = server.model_dump(mode="json", by_alias=True, exclude_none=True)
    http = payload.get("http")
    if isinstance(http, dict) and isinstance(http.get("url"), str) and "?" in http["url"]:
        http["url"] = http["url"].split("?", 1)[0] + "?redacted"
    return payload


def _load_mcp_config_for_api(manager: MCPManager):
    """Load the operator-owned MCP config without leaking parser failures as 500s."""
    try:
        return load_mcp_config(manager.config_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MCP_CONFIG_NOT_FOUND",
                "message": "MCP configuration is unavailable",
                "reason": str(exc),
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MCP_CONFIG_UNREADABLE",
                "message": "MCP configuration could not be read",
                "reason": str(exc),
            },
        ) from exc
    except ValueError as exc:
        reason = str(exc)
        if len(reason) > 4000:
            reason = f"{reason[:4000]}\n... validation output truncated"
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MCP_CONFIG_INVALID",
                "message": "MCP configuration is invalid",
                "reason": reason,
            },
        ) from exc


def _server_record(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    server = config.servers.get(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    status = next((item for item in manager.status_snapshot()["records"] if item["server"] == server_id), None)
    return {"id": server_id, "config": _public_server_config(server), "status": status}


@router.get("/mcp/servers")
def list_mcp_servers() -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    statuses = {item["server"]: item for item in manager.status_snapshot()["records"]}
    return {
        "servers": [
            {"id": server_id, "config": _public_server_config(server), "status": statuses.get(server_id)}
            for server_id, server in sorted(config.servers.items())
        ]
    }


@router.post("/mcp/servers", status_code=201)
def create_mcp_server(payload: dict[str, Any]) -> dict[str, Any]:
    server_id = str(payload.get("id") or "").strip()
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raw_config = {key: value for key, value in payload.items() if key != "id"}
    try:
        server = MCPServerConfig.model_validate(raw_config)
        action = upsert_mcp_server(_catalog_runner().config_path, server_id, server)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _catalog_runner().refresh()
    return {"action": action, "server": _server_record(server_id)}


@router.get("/mcp/servers/{server_id}")
def get_mcp_server(server_id: str) -> dict[str, Any]:
    return _server_record(server_id)


@router.patch("/mcp/servers/{server_id}")
def update_mcp_server(server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        patch_mcp_server(_catalog_runner().config_path, server_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _catalog_runner().refresh()
    return {"server": _server_record(server_id)}


@router.delete("/mcp/servers/{server_id}")
def delete_managed_mcp_server(server_id: str) -> dict[str, Any]:
    return remove_mcp_server(server_id)


@router.post("/mcp/servers/{server_id}/test")
def test_mcp_server(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    try:
        discovery = manager.test_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    return discovery.model_dump(mode="json")


@router.post("/mcp/servers/{server_id}/refresh")
def refresh_one_mcp_server(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    config, _ = _load_mcp_config_for_api(manager)
    if server_id not in config.servers:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    manager.refresh()
    return _server_record(server_id)


@router.get("/mcp/servers/{server_id}/tools")
def list_mcp_server_tools(server_id: str) -> dict[str, Any]:
    manager = _catalog_runner()
    try:
        discovery = manager.test_server(server_id)
        config, _ = _load_mcp_config_for_api(manager)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    enabled = set(config.servers[server_id].enabled_tools)
    return {
        "server_id": server_id,
        "status": discovery.status,
        "protocol_version": discovery.protocol_version,
        "server_info": discovery.server_info,
        "error": discovery.error,
        "tools": [
            {**tool.model_dump(mode="json"), "enabled": not enabled or tool.name in enabled}
            for tool in discovery.tools
        ],
    }


@router.post("/mcp/servers/{server_id}/tools/{tool_name:path}/test")
def test_mcp_method(server_id: str, tool_name: str, payload: MCPMethodTestRequest) -> dict[str, Any]:
    """Execute one real method through the shared production MCP manager."""
    manager = _catalog_runner()
    snapshot = manager.ensure_catalog()
    server = manager.config.servers.get(server_id) if manager.config else None
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    if not server.enabled:
        raise HTTPException(status_code=409, detail=f"MCP server is disabled: {server_id}")
    route = next((item for item in snapshot.routes if item.server_id == server_id and item.method == tool_name), None)
    if route is None:
        # One controlled refresh is allowed before declaring the catalog stale.
        snapshot = manager.refresh()
        route = next((item for item in snapshot.routes if item.server_id == server_id and item.method == tool_name), None)
    if route is None:
        raise HTTPException(status_code=404, detail=f"MCP method is not discovered: {server_id}.{tool_name}")
    risk = manager.policy.risk_for(server=server, method=route.method)
    if risk == "destructive":
        raise HTTPException(status_code=403, detail="destructive MCP method tests are forbidden")
    if risk == "active" and not payload.confirm_active:
        raise HTTPException(status_code=409, detail="active MCP method test requires confirm_active=true")
    method_policy = server.methods.get(route.method)
    allowed_modes = method_policy.modes if method_policy and method_policy.modes is not None else server.visibility.modes
    task = TGATask(
        id="mcp_method_test", name="MCP method test", mode=allowed_modes[0], target="mcp://local",
        goal="Explicit operator-authorized MCP method test", mcp_servers=[server_id],
        allow_active_scan=payload.confirm_active,
    )
    outcome = manager.call_tool(
        task=task, route=route, arguments=payload.arguments,
        catalog_version=snapshot.version, trace_id=f"trace_method_test_{os.urandom(8).hex()}",
    )
    _append_mcp_method_test_audit(
        server_id=server_id,
        method=tool_name,
        risk=risk,
        confirm_active=payload.confirm_active,
        arguments=payload.arguments,
        outcome=outcome.model_dump(mode="json"),
    )
    preview = redact_sensitive({"content": outcome.content, "structured_content": outcome.structured_content})
    encoded = json.dumps(preview, ensure_ascii=False, default=str)
    return {
        "ok": outcome.ok,
        "server": server_id,
        "method": tool_name,
        "trace_id": outcome.trace_id,
        "request_id": outcome.request_id,
        "catalog_version": outcome.catalog_version,
        "protocol_version": outcome.protocol_version,
        "server_info": outcome.server_info,
        "timings": outcome.timings,
        "is_error": outcome.is_error,
        "error": outcome.error.model_dump(mode="json") if outcome.error else None,
        "content_preview": encoded[:12000],
        "truncated": len(encoded) > 12000 or outcome.artifact_truncated or outcome.output_truncated,
        "explicit_active_authorization": bool(risk == "active" and payload.confirm_active),
    }


def _append_mcp_method_test_audit(
    *, server_id: str, method: str, risk: str, confirm_active: bool,
    arguments: dict[str, Any], outcome: dict[str, Any],
) -> None:
    record = {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": "MCP_METHOD_TEST",
        "server": server_id,
        "method": method,
        "risk": risk,
        "explicit_active_authorization": bool(risk == "active" and confirm_active),
        "arguments": redact_sensitive(arguments),
        "ok": outcome.get("ok"),
        "request_id": outcome.get("request_id"),
        "trace_id": outcome.get("trace_id"),
        "timings": outcome.get("timings") or {},
        "error": outcome.get("error"),
    }
    audit_path = _run_root() / "mcp-method-tests.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with _runner_lock:
        with audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


@router.get("/mcp/images")
def list_local_mcp_images() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "image", "ls", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Docker CLI is unavailable") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=503, detail=result.stderr.strip() or "docker image ls failed")
    images = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        repository, tag = item.get("Repository"), item.get("Tag")
        item["name"] = f"{repository}:{tag}" if repository and tag else item.get("ID", "")
        images.append(item)
    return {"images": images}


@router.post("/mcp/images/{image:path}/inspect")
def inspect_local_mcp_image(image: str) -> dict[str, Any]:
    if not image or any(character in image for character in ("\x00", "\r", "\n")):
        raise HTTPException(status_code=400, detail="invalid Docker image name")
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False, shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Docker CLI is unavailable") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=404, detail="local Docker image was not found; TGA did not pull it")
    try:
        details = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Docker returned invalid inspect output") from exc
    return {"image": image, "local": True, "details": details[0] if details else {}}


@router.delete("/tools/mcp/{server_id}")
def remove_mcp_server(server_id: str) -> dict[str, Any]:
    """Remove one explicit server entry; the underlying Docker image is retained."""
    manager = _catalog_runner()
    try:
        removed = delete_mcp_server(manager.config_path, server_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}")
    manager.refresh()
    return {
        "deleted": True,
        "server_id": server_id,
        "image_deleted": False,
        "catalog": manager.status_snapshot(),
    }


@router.patch("/tools/mcp/{server_id}/enabled")
def change_mcp_server_enabled(server_id: str, request: MCPEnabledRequest) -> dict[str, Any]:
    """Enable or disable one configured server and refresh dynamic discovery."""
    manager = _catalog_runner()
    try:
        enabled = set_mcp_server_enabled(manager.config_path, server_id, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"MCP server is not configured: {server_id}") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manager.refresh()
    return {
        "server_id": server_id,
        "enabled": enabled,
        "catalog": manager.status_snapshot(),
    }


@router.post("/tools/mcp/import")
@router.post("/mcp/images/import")
async def import_mcp_package(request: Request) -> dict[str, Any]:
    """Build/load one operator-selected MCP package and add it to mcp.json.

    The endpoint intentionally accepts a raw body rather than multipart data:
    clients cannot submit Docker arguments, tags, mounts or environment values.
    """
    encoded_name = request.headers.get("x-tga-filename", "")
    filename = unquote(encoded_name)
    if not filename or Path(filename).name != filename or "\x00" in filename:
        raise HTTPException(status_code=400, detail="A valid X-TGA-Filename header is required")
    try:
        max_bytes = int(os.environ.get("TGA_MCP_IMPORT_MAX_BYTES", str(DEFAULT_MAX_PACKAGE_BYTES)))
    except ValueError:
        max_bytes = DEFAULT_MAX_PACKAGE_BYTES
    max_bytes = max(1, min(max_bytes, DEFAULT_MAX_PACKAGE_BYTES))
    upload_root = (_run_root() / ".mcp-imports").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=Path(filename).suffix, dir=upload_root)
    received = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > max_bytes:
                    raise HTTPException(status_code=413, detail=f"MCP package exceeds the {max_bytes} byte limit")
                output.write(chunk)
        manager = _catalog_runner()
        importer = MCPImageImporter(config_path=manager.config_path, max_package_bytes=max_bytes)
        try:
            result = await asyncio.to_thread(importer.import_package, temporary_name, filename)
        except MCPImportError as exc:
            status = 503 if exc.code in {"DOCKER_UNAVAILABLE", "DOCKER_TIMEOUT"} else 400
            raise HTTPException(status_code=status, detail=f"{exc.code}: {exc}") from exc
        await asyncio.to_thread(manager.refresh)
        result.catalog = manager.status_snapshot()
        return result.model_dump(mode="json")
    finally:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass


@router.get("/tasks/{task_id}/artifacts/{artifact_id}", response_model=None)
def artifact(
    task_id: str,
    artifact_id: str,
    download: bool = False,
    query: str | None = None,
    section: str | None = None,
    offset: int = 0,
    limit: int = 6000,
):
    """Return a bounded, redacted preview unless an explicit download is requested.

    The preview is the product-facing endpoint.  Raw artifact delivery remains
    explicit for already-authorized users and is never embedded in a runtime
    list or report.
    """
    snapshot = _snapshot(task_id)
    item = next((value for value in snapshot["artifacts"] if value.get("id") == artifact_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    task = TGATask.model_validate(snapshot["task"])
    root = task_artifact_root(_task_root(task_id), task)
    path = (root / str(item.get("path") or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid artifact path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    if download:
        return FileResponse(path, filename=path.name)
    if query or section or offset:
        store = EvidenceStore(_task_root(task_id) / "evidence.db")
        try:
            index = store.get_artifact_index(artifact_id)
        finally:
            store.close()
        if index is None:
            index = build_artifact_index(
                task_id=task_id,
                artifact_id=artifact_id,
                raw=path.read_bytes(),
                document_type="html" if path.suffix.casefold() in {".html", ".htm"} else None,
            )
        retrieval = retrieve_segments(
            index,
            query=(query or "")[:256] or None,
            section=(section or "")[:256] or None,
            offset=max(0, offset),
            limit=max(1, min(limit, 12_000)),
        )
        for match in retrieval["matches"]:
            match["text"], _ = _redact_artifact_text(match["text"])
        return JSONResponse({"artifact": {"id": artifact_id, "kind": item.get("kind")}, "retrieval": retrieval})
    return JSONResponse(_artifact_preview(item, path))


def _artifact_preview(item: dict[str, Any], path: Path, *, byte_limit: int = 16_384) -> dict[str, Any]:
    raw = path.read_bytes()[: byte_limit + 1]
    truncated = path.stat().st_size > byte_limit
    binary = b"\x00" in raw
    if binary:
        excerpt = "[binary artifact omitted from inline preview]"
    else:
        excerpt = raw.decode("utf-8", errors="replace")
    redacted, redaction_count = _redact_artifact_text(excerpt)
    return {
        "artifact": {key: item.get(key) for key in ("id", "kind", "tool", "target", "created_at", "sha256")},
        "preview": redacted,
        "truncated": truncated,
        "binary": binary,
        "redactions": redaction_count,
        "byte_limit": byte_limit,
        "download_url": None,
    }


def _redact_artifact_text(value: str) -> tuple[str, int]:
    import re

    patterns = (
        r"(?im)^\s*((?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*)[^\r\n]+",
        r"(?i)\b((?:token|secret|api[_-]?key|password)\s*[=:]\s*)([^\s,;}&]+)",
        r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
    )
    count = 0
    for pattern in patterns:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"{match.group(1)}[REDACTED]"
        value = re.sub(pattern, replace, value)
    return value, count


@router.post("/tasks/{task_id}/control")
def control(task_id: str, payload: ControlRequest) -> dict[str, Any]:
    if payload.action == "resume" and not model_config_status()["configured"]:
        raise HTTPException(status_code=409, detail="model_not_configured")
    _snapshot(task_id)
    result = _runtime_command("control_session", task_id, payload.model_dump(exclude_none=True))
    if result.get("accepted") and payload.action == "resume":
        result["scheduled"] = _schedule_runtime_runner(task_id)
    return result


@router.post("/tasks/{task_id}/hints")
def add_hint(task_id: str, payload: HintRequest) -> dict[str, Any]:
    _snapshot(task_id)
    return _runtime_command("add_hint", task_id, {"content": payload.content})


def _runtime_command(method_name: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Delegate all lifecycle mutations to the Manager, never directly to SQLite."""
    try:
        service = TaskRuntimeService(run_root=_run_root())
        result = service.command(method_name, task_id, **payload)
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail="v2 runtime manager is not available yet") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result if isinstance(result, dict) else {"status": "accepted"}


def _schedule_runtime_runner(task_id: str) -> bool:
    """Start at most one in-process Manager loop per task.

    The long-running solver loop must never occupy the HTTP request that
    created the session.  A process restart is safe: the in-memory set is
    cleared and the durable session record remains the source of truth.
    """
    with _runner_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)

    def runner() -> None:
        try:
            _runtime_command("run_session", task_id, {})
        finally:
            with _runner_lock:
                _running_tasks.discard(task_id)

    threading.Thread(target=runner, name=f"tga-runtime-{task_id}", daemon=True).start()
    return True
