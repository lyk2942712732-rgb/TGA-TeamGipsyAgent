"""Application service for creating and scheduling a current Runtime task."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from tga.contracts import ExecutionPolicy, MCPCapabilitySnapshot, MCPCapabilityTool, TGATask
from tga.inputs import SessionWorkspace
from tga.models.bootstrap import model_config_status
from tga.modes import mode_profile, normalize_mode, validate_task_profile
from tga.network_policy import input_network_seeds
from tga.runtime.service import TaskRuntimeService
from tga.tools.mcp_manager import MCPManager


class TaskCreationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CreateTaskCommand:
    task_id: str | None
    name: str
    mode: str
    goal: str | None
    mode_options: dict[str, Any]
    input_text: str
    file_ids: list[str]
    execution_policy: ExecutionPolicy


@dataclass(frozen=True)
class CreatedTask:
    task_id: str
    status: str
    scheduled: bool
    mcp_capabilities: MCPCapabilitySnapshot


class TaskCreationService:
    def __init__(
        self,
        *,
        run_root: str | Path,
        mcp_manager: MCPManager,
        schedule: Callable[[str], bool],
        runtime_service: TaskRuntimeService | None = None,
        model_status: Callable[[], dict[str, Any]] = model_config_status,
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.mcp_manager = mcp_manager
        self.schedule = schedule
        self.runtime_service = runtime_service or TaskRuntimeService(run_root=self.run_root)
        self.model_status = model_status

    def create(self, command: CreateTaskCommand) -> CreatedTask:
        provider = self.model_status()
        if not provider.get("configured"):
            raise TaskCreationError("MODEL_NOT_CONFIGURED", "model_not_configured")
        if provider.get("verification_status") != "verified":
            raise TaskCreationError("MODEL_NOT_VERIFIED", "model_not_verified")
        verification = provider.get("verification") or {}
        try:
            mode = normalize_mode(command.mode)
        except ValueError as exc:
            raise TaskCreationError("INVALID_MODE", str(exc)) from exc

        task_id = command.task_id or f"task_{uuid4().hex[:12]}"
        task_root = self.runtime_service.task_root(task_id)
        if task_root.exists():
            raise TaskCreationError("SESSION_EXISTS", "Session id already exists")

        cleanup_stages: list[Path] = []
        try:
            session_input, cleanup_stages = SessionWorkspace(task_root).claim_staged(
                staging_root=self.run_root / "_input_staging",
                prompt=command.input_text,
                asset_ids=command.file_ids,
            )
            seed_origins, entry_url = input_network_seeds(command.input_text)
            policy = command.execution_policy.model_copy(deep=True)
            policy.network.seed_origins = seed_origins
            capabilities = build_mcp_capability_snapshot(self.mcp_manager)
            task = TGATask(
                id=task_id,
                name=command.name.strip(),
                mode=mode,
                goal=(command.goal or mode_profile(mode).default_goal).strip(),
                mode_config={**command.mode_options, "mode": mode},
                execution_policy=policy,
                session_input=session_input,
                task_entry_url=entry_url,
                mcp_capabilities=capabilities,
                model_snapshot={
                    "provider": provider.get("provider") or "openai-compatible",
                    "model": provider.get("model") or "",
                    "capability_fingerprint": verification.get("capability_fingerprint") or "",
                    "verification_id": verification.get("id") or "",
                    "verified_at": verification.get("verified_at") or "",
                    "capabilities": verification.get("capabilities") or {},
                    "max_output_tokens": provider.get("max_output_tokens"),
                    "timeout_seconds": provider.get("timeout_seconds"),
                    "temperature": provider.get("temperature"),
                    "reasoning_mode": provider.get("reasoning_mode") or "auto",
                },
                schema_version=5,
            )
            validate_task_profile(task)
            result = self.runtime_service.create_task(task)
            if not result.get("accepted"):
                raise TaskCreationError(
                    "SESSION_START_REJECTED",
                    str(result.get("reason") or "session did not start"),
                )
            scheduled = self.schedule(task.id)
        except TaskCreationError:
            shutil.rmtree(task_root, ignore_errors=True)
            raise
        except (OSError, ValueError) as exc:
            shutil.rmtree(task_root, ignore_errors=True)
            raise TaskCreationError("SESSION_CREATE_FAILED", str(exc)) from exc
        except Exception:
            shutil.rmtree(task_root, ignore_errors=True)
            raise

        for stage in cleanup_stages:
            shutil.rmtree(stage, ignore_errors=True)
        return CreatedTask(
            task_id=task.id,
            status=str(result["status"]),
            scheduled=scheduled,
            mcp_capabilities=task.mcp_capabilities,
        )


def build_mcp_capability_snapshot(manager: MCPManager) -> MCPCapabilitySnapshot:
    snapshot = manager.ensure_catalog()
    enabled = {
        server_id
        for server_id, server in (manager.config.servers.items() if manager.config else [])
        if server.enabled
    }
    routes = [item for item in snapshot.routes if item.server_id in enabled]
    server_ids = sorted(
        {
            item.server_id
            for item in snapshot.servers
            if item.server_id in enabled
            and (item.status in {"discovered", "reachable"} or item.error is None)
        }
        | {item.server_id for item in routes}
    )
    return MCPCapabilitySnapshot(
        catalog_version=snapshot.version,
        server_ids=server_ids,
        tools=[MCPCapabilityTool(**item.model_dump(mode="json")) for item in routes],
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
