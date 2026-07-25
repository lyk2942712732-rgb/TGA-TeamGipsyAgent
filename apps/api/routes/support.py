"""Shared HTTP DTOs, read projections, and runtime scheduling primitives."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from urllib.parse import urlsplit

from tga.contracts import ExecutionPolicy, TGATask
from tga.evidence.store import EvidenceStore
from tga.evidence.database import DatabaseSchemaVersionError
from tga.runtime.coordinator import SessionTransitionError
from tga.runtime.scheduler import RuntimeScheduler
from tga.runtime.service import TaskRuntimeService, UnsupportedTaskSchemaError, require_current_task_schema
from tga.tools.mcp_manager import MCPManager


_scheduler: RuntimeScheduler | None = None
_scheduler_root: Path | None = None


def _api_error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _unsupported_schema_detail(exc: UnsupportedTaskSchemaError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": str(exc),
        "schema_version": exc.schema_version,
        "required_schema_version": 5,
    }


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "cancel", "approve_action", "reject_action"]
    action_id: str | None = None


class HintRequest(BaseModel):
    content: str = Field(min_length=1, max_length=800)


class StartRequest(BaseModel):
    initial_hint: str | None = Field(default=None, max_length=800)


class CreateSessionInputRequest(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    text: str = Field(default="", max_length=16_384)
    file_ids: list[str] = Field(default_factory=list, alias="fileIds", max_length=64)


class CreateTaskRequest(BaseModel):
    """Schema-v5 product request."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=255)
    mode: str
    goal: str | None = Field(default=None, max_length=8000)
    mode_options: dict[str, Any] = Field(default_factory=dict, alias="modeOptions")
    input: CreateSessionInputRequest
    execution_policy: ExecutionPolicy = Field(alias="executionPolicy")
    selected_skills: list[str] | None = Field(default=None, alias="selectedSkills", max_length=3)


class SkillPreviewRequest(BaseModel):
    """Draft task fields needed by the authoritative Skill selector."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    mode: str
    goal: str = Field(min_length=1, max_length=8000)
    mode_options: dict[str, Any] = Field(default_factory=dict, alias="modeOptions")
    prompt: str = Field(default="", max_length=16_384)
    file_names: list[str] = Field(default_factory=list, alias="fileNames", max_length=64)
    execution_policy: ExecutionPolicy = Field(alias="executionPolicy")
    selected_skills: list[str] | None = Field(default=None, alias="selectedSkills", max_length=3)


class LLMSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}

    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=255)
    api_key: SecretStr | None = Field(default=None, max_length=16_384)
    supports_vision: bool | None = None
    max_output_tokens: int | None = Field(default=None, ge=256, le=16_384)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_mode: Literal["auto", "enabled", "disabled"] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        clean = value.strip().rstrip("/")
        parsed = urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query, or fragment")
        return clean

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("model must not be blank")
        return clean


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
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail=_unsupported_schema_detail(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return _normalize_snapshot(snapshot)


def _require_current_task(task_id: str) -> None:
    try:
        store = EvidenceStore(_task_root(task_id) / "evidence.db")
    except DatabaseSchemaVersionError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "SCHEMA_VERSION_UNSUPPORTED",
            "message": f"task schema {exc.schema_version} is not executable; migrate it to schema 5",
            "schema_version": exc.schema_version,
            "required_schema_version": 5,
        }) from exc
    try:
        task = store.get_task(task_id)
    finally:
        store.close()
    if task is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        require_current_task_schema(task)
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail=_unsupported_schema_detail(exc)) from exc


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
        return {
            str(key): (
                _public_action_arguments(item)
                if key == "arguments" and isinstance(item, dict)
                else _compact_public_payload(item)
            )
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_compact_public_payload(item) for item in value]
    return value


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project the durable v2 repository into its public UI contract."""
    events = _runtime_events(snapshot)
    latest_seq = max((event["seq"] for event in events), default=0)
    session = snapshot["session"]
    runtime = snapshot.get("runtime") or {}
    solvers = snapshot.get("solvers") or []
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
            "started_at": session.get("started_at"),
            "finished_at": session.get("finished_at"),
        },
        "solvers": solvers,
        "challenge": snapshot.get("challenge") or {},
        "runtime": {
            "memory": runtime.get("memory") or snapshot.get("memory") or snapshot.get("memory_entries") or [],
            "strategy_cards": runtime.get("strategy_cards") or snapshot.get("strategy_cards") or [],
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
        "context_metrics": _normalize_context_metrics(snapshot.get("context_metrics") or []),
        "events": events,
        "latest_seq": latest_seq,
        "schema_version": 2,
    }
    return result


def _normalize_context_metrics(metrics: list[Any]) -> list[dict[str, Any]]:
    """Keep optional provider usage fields absent when old rows persisted null."""
    normalized: list[dict[str, Any]] = []
    for metric in metrics[-100:]:
        if not isinstance(metric, dict):
            continue
        # The Web contract makes token counters optional, not nullable. Older
        # snapshots wrote them before a provider response was available.
        normalized.append(_compact_public_payload(metric))
    return normalized


def _normalize_action(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten the persisted tool result into the Web action projection."""
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    summary = result.get("summary") or item.get("summary") or ""
    public_fields = {
        "id", "solver_id", "kind", "capability", "target", "rationale", "risk",
        "strategy_card_id", "strategy_step_id", "expected_outcome", "retry_reason",
        "alternative_analysis", "approval_expires_at", "input_id", "target_ref",
        "actual_target", "authorization", "provenance", "status", "created_at", "updated_at",
    }
    return {
        **{key: value for key, value in item.items() if key in public_fields},
        "arguments": _public_action_arguments(item.get("arguments") or {}),
        "effect": item.get("effect") or {},
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
        if _sensitive_name(str(key)):
            public[key] = "[REDACTED]"
        elif key == "body":
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
        elif isinstance(value, dict):
            public[key] = _public_action_arguments(value)
        elif isinstance(value, list):
            public[key] = [_public_argument_value(item) for item in value[:100]]
        else:
            public[key] = value
    return public


def _public_argument_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _public_action_arguments(value)
    if isinstance(value, list):
        return [_public_argument_value(item) for item in value[:100]]
    return value


def _sensitive_name(name: str) -> bool:
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    return any(part in normalized for part in (
        "authorization", "cookie", "token", "secret", "password", "passwd", "apikey",
    ))


def _runtime_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """AgentEvent is the only cursor source for a v2 session."""
    source_events = snapshot.get("agent_events") or []
    normalized = [
        _normalize_event(event, index + 1)
        for index, event in enumerate(source_events)
    ]
    return sorted(normalized, key=lambda event: event["seq"])


def _runtime_command(method_name: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Delegate all lifecycle mutations to the Manager, never directly to SQLite."""
    try:
        service = TaskRuntimeService(run_root=_run_root())
        result = service.command(method_name, task_id, **payload)
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail=_unsupported_schema_detail(exc)) from exc
    except SessionTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "from_status": exc.from_status, "to_status": exc.to_status, "message": str(exc)},
        ) from exc
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail="v2 runtime manager is not available yet") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result if isinstance(result, dict) else {"status": "accepted"}


def _schedule_runtime_runner(task_id: str) -> bool:
    return _runtime_scheduler().schedule(task_id)


def _runtime_scheduler() -> RuntimeScheduler:
    global _scheduler, _scheduler_root
    root = _run_root().resolve()
    if _scheduler is None or _scheduler_root != root:
        service = TaskRuntimeService(run_root=root)
        _scheduler = RuntimeScheduler(run_root=root, run_task=service.run_task)
        _scheduler_root = root
    return _scheduler


def _catalog_runner() -> MCPManager:
    """Return the product MCP manager backed only by explicit mcp.json."""
    from tga.runtime.manager import get_manager

    return get_manager().mcp_manager
