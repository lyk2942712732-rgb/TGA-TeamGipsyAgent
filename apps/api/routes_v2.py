"""The sole public API for the durable TGA v2 runtime."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tga.capabilities.mcp import health_snapshot, tool_catalog_snapshot
from tga.capabilities.registry import build_default_registry
from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.models.bootstrap import model_config_status
from tga.reporting.markdown_report import render_markdown_report
from tga.tools.bootstrap import discover_mcp_security_hub_root
from tga.tools.mcp_catalog import discover_mcp_security_hub
from tga.tools.tool_runner import ToolRunner


router = APIRouter(prefix="/v2", tags=["runtime-v2"])


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


class CreateTaskRequest(BaseModel):
    task: TGATask
    initial_hint: str | None = Field(default=None, max_length=800)


class LLMSettingsRequest(BaseModel):
    base_url: str
    api_key: str
    model: str


_runner_lock = threading.Lock()
_running_tasks: set[str] = set()


def _run_root() -> Path:
    return Path(os.environ.get("TGA_RUN_ROOT", "runs"))


def _task_root(task_id: str) -> Path:
    root = _run_root().resolve()
    candidate = (root / task_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid task id") from exc
    return candidate


def _snapshot(task_id: str) -> dict[str, Any]:
    db_path = _task_root(task_id) / "evidence.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="session not found")
    store = EvidenceStore(db_path)
    try:
        snapshot = store.get_session_snapshot(task_id)
    finally:
        store.close()
    if not snapshot.get("task"):
        raise HTTPException(status_code=404, detail="session not found")
    if not snapshot.get("session"):
        raise HTTPException(status_code=404, detail="v2 session not found")
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
    result = {
        "task": snapshot.get("task") or {},
        "session": {
            "status": session["status"],
            "turn_count": int(session["turn_count"]),
            "max_turns": int(session["max_turns"]),
            "active_solver_id": session.get("active_solver_id"),
            "stop_reason": session.get("stop_reason"),
        },
        "solvers": snapshot.get("solvers") or [],
        "challenge": snapshot.get("challenge") or {},
        "subagents": snapshot.get("subagents") or [],
        "board": {
            "hypotheses": board.get("hypotheses") or snapshot.get("hypotheses") or [],
            "memory": board.get("memory") or snapshot.get("memory") or snapshot.get("memory_entries") or [],
        },
        "actions": snapshot.get("actions") or [],
        "flags": snapshot.get("flags") or [],
        "findings": snapshot.get("findings") or [],
        "artifacts": snapshot.get("artifacts") or [],
        "events": events,
        "latest_seq": latest_seq,
        "schema_version": 2,
    }
    return result


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


@router.post("/tasks")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    """Create and initialize a session atomically from the UI's perspective."""
    task = payload.task
    task_root = _task_root(task.id)
    store = EvidenceStore(task_root / "evidence.db")
    try:
        if store.task_snapshot(task.id).get("task"):
            raise HTTPException(status_code=409, detail="task id already exists")
        store.create_task(task)
    finally:
        store.close()
    result = _runtime_command("start_session", task.id, {"initial_hint": payload.initial_hint})
    if not result.get("accepted"):
        raise HTTPException(status_code=409, detail=str(result.get("reason") or "session did not start"))
    return {"task_id": task.id, "status": result["status"], "scheduled": _schedule_runtime_runner(task.id)}


@router.get("/tasks")
def list_tasks() -> dict[str, list[dict[str, Any]]]:
    tasks: list[dict[str, Any]] = []
    run_root = _run_root()
    if not run_root.exists():
        return {"tasks": tasks}
    for child in sorted(run_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        db_path = child / "evidence.db"
        if not child.is_dir() or child.name.startswith(".") or not db_path.is_file():
            continue
        try:
            snapshot = _snapshot(child.name)
        except HTTPException:
            continue
        task = snapshot["task"]
        tasks.append({
            "task_id": child.name,
            "name": task.get("name") or child.name,
            "mode": task.get("mode") or "ctf",
            "target": task.get("target") or "",
            "created_at": snapshot["events"][0]["created_at"] if snapshot["events"] else "",
            "status": snapshot["session"]["status"],
            "flags": len(snapshot["flags"]),
            "findings": len(snapshot["findings"]),
            "artifacts": len(snapshot["artifacts"]),
        })
    return {"tasks": tasks}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    task_root = _task_root(task_id)
    if task_root.exists():
        shutil.rmtree(task_root)
    return {"task_id": task_id, "deleted": True}


@router.get("/tasks/{task_id}/report")
def task_report(task_id: str) -> FileResponse:
    snapshot = _snapshot(task_id)
    report_path = _task_root(task_id) / "reports" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(snapshot), encoding="utf-8")
    return FileResponse(report_path, media_type="text/markdown")


@router.get("/settings/llm")
def llm_settings() -> dict[str, Any]:
    status = model_config_status()
    return {
        "configured": bool(status["configured"]),
        "base_url": str(status.get("base_url") or ""),
        "model": str(status.get("model") or ""),
        "api_key_set": bool(os.environ.get("TGA_LLM_API_KEY")),
    }


@router.post("/settings/llm")
def update_llm_settings(payload: LLMSettingsRequest) -> dict[str, Any]:
    os.environ["TGA_LLM_BASE_URL"] = payload.base_url.strip()
    os.environ["TGA_LLM_API_KEY"] = payload.api_key.strip()
    os.environ["TGA_LLM_MODEL"] = payload.model.strip()
    return llm_settings()


@router.post("/tasks/{task_id}/start")
def start_session(task_id: str, payload: StartRequest) -> dict[str, Any]:
    """Resume initialization for a v2 session after a process restart."""
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


def _catalog_runner() -> ToolRunner | None:
    """Create a runner only for B's read-only catalog and health adapters."""
    root = discover_mcp_security_hub_root()
    if root is None:
        return None
    try:
        return ToolRunner(
            catalog=discover_mcp_security_hub(root),
            artifact_store=_CatalogOnlyArtifactStore(),  # type: ignore[arg-type]
        )
    except (OSError, ValueError):
        return None


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


@router.get("/tasks/{task_id}/artifacts/{artifact_id}", response_model=None)
def artifact(task_id: str, artifact_id: str, download: bool = False):
    """Return a bounded, redacted preview unless an explicit download is requested.

    The preview is the product-facing endpoint.  Raw artifact delivery remains
    explicit for already-authorized users and is never embedded in a runtime
    list or report.
    """
    snapshot = _snapshot(task_id)
    item = next((value for value in snapshot["artifacts"] if value.get("id") == artifact_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    root = (_task_root(task_id) / "artifacts").resolve()
    path = (root / str(item.get("path") or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid artifact path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")
    if download:
        return FileResponse(path, filename=path.name)
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
        module = importlib.import_module("tga.runtime.manager")
        manager = getattr(module, "get_manager")()
        method = getattr(manager, method_name)
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail="v2 runtime manager is not available yet") from exc
    try:
        result = method(task_id=task_id, **payload)
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
