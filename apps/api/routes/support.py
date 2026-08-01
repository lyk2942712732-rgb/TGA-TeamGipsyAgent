"""Shared HTTP DTOs, read projections, and runtime scheduling primitives."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from urllib.parse import urlsplit

from tga.contracts import ExecutionPolicy
from tga.application.commands import RuntimeCommands
from tga.application.queries import RuntimeQueries
from tga.runtime.scheduler import RuntimeScheduler
from tga.runtime.service import TaskRuntimeService, UnsupportedTaskSchemaError
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
        "required_schema_version": 6,
    }


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "cancel", "approve_action", "reject_action"]
    action_id: str | None = None


class StartRequest(BaseModel):
    initial_hint: str | None = Field(default=None, max_length=800)


class CreateSessionInputRequest(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    text: str = Field(default="", max_length=16_384)
    file_ids: list[str] = Field(default_factory=list, alias="fileIds", max_length=64)


class CreateTaskRequest(BaseModel):
    """Schema-v6 product request."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, alias="workspaceId", min_length=1, max_length=255)
    mode: str
    goal: str | None = Field(default=None, max_length=8000)
    mode_options: dict[str, Any] = Field(default_factory=dict, alias="modeOptions")
    input: CreateSessionInputRequest
    execution_policy: ExecutionPolicy = Field(alias="executionPolicy")
    selected_skills: list[str] | None = Field(default=None, alias="selectedSkills", max_length=3)
    preflight_fingerprint: str | None = Field(
        default=None, alias="preflightFingerprint", pattern=r"^[a-f0-9]{64}$"
    )


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


def _runtime_queries() -> RuntimeQueries:
    return RuntimeQueries(run_root=_run_root())


def _application_commands() -> RuntimeCommands:
    return RuntimeCommands(run_root=_run_root(), service_type=TaskRuntimeService)


def _task_root(task_id: str) -> Path:
    try:
        return RuntimeQueries(run_root=_run_root()).task_root(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid task id") from exc


def _snapshot(task_id: str) -> dict[str, Any]:
    try:
        projection = RuntimeQueries(run_root=_run_root()).snapshot(task_id)
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail=_unsupported_schema_detail(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return projection.model_dump(mode="json")


def _require_current_task(task_id: str) -> None:
    try:
        RuntimeQueries(run_root=_run_root()).task_schema_version(task_id)
    except UnsupportedTaskSchemaError as exc:
        raise HTTPException(status_code=409, detail=_unsupported_schema_detail(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


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
