"""Frozen execution contracts shared by every tool backend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.runtime.tooling.requests import GovernedAction
from tga.runtime.tooling.results import ExecutionError


ExecutionBackendKind = Literal[
    "host_control", "host_retrieval", "sandbox", "remote_mcp"
]


class AuthorizedExecutionRequest(BaseModel):
    """The immutable request emitted after the governance decision is frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    capability: str
    backend: ExecutionBackendKind
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str
    solver_id: str
    solver_run_id: str | None = None
    intent_id: str | None = None
    execution_profile_id: str | None = None
    sandbox_config_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fencing_token: int | None = Field(default=None, ge=1)
    idempotency_key: str
    resolved_target: str | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_action(
        cls,
        action: GovernedAction,
        *,
        backend: ExecutionBackendKind,
    ) -> "AuthorizedExecutionRequest":
        return cls(
            action_id=action.id,
            capability=action.capability,
            backend=backend,
            arguments=action.normalized_arguments,
            task_id=action.context.task_id,
            solver_id=action.context.solver_id,
            solver_run_id=action.context.run_id,
            intent_id=action.context.intent_id,
            execution_profile_id=action.execution_profile_id,
            sandbox_config_digest=action.sandbox_config_digest,
            fencing_token=action.context.run_fencing_token,
            idempotency_key=action.idempotency_key or action.id,
            resolved_target=action.resolved_target,
            execution_metadata={
                **action.execution_metadata,
                "provider_tool_name": action.provider_tool_name,
                "tool_call_id": action.tool_call_id,
            },
        )


class ProducedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    kind: str = "file"
    media_type: str | None = None


class ExecutionResult(BaseModel):
    """Backend-neutral raw result before Artifact ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    status: Literal[
        "pending_approval", "succeeded", "failed", "blocked", "rejected",
        "expired", "cancelled",
    ]
    exit_code: int | None = None
    stdout_preview: str = Field(default="", max_length=262_144)
    stderr_preview: str = Field(default="", max_length=262_144)
    started_at: str | None = None
    finished_at: str | None = None
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    produced_files: tuple[ProducedFile, ...] = ()
    structured_result: dict[str, Any] = Field(default_factory=dict)
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list, max_length=1_024)
    error: ExecutionError | None = None

    @property
    def output(self) -> dict[str, Any]:
        """Project the backend result into the model-facing gateway payload."""
        payload = dict(self.structured_result)
        payload.setdefault("ok", self.status == "succeeded")
        payload.setdefault("status", self.status)
        if self.stdout_preview:
            payload.setdefault("stdout", self.stdout_preview)
        if self.stderr_preview:
            payload.setdefault("stderr", self.stderr_preview)
        if self.exit_code is not None:
            payload.setdefault("exit_code", self.exit_code)
        if self.artifact_ids:
            payload.setdefault("artifact_ids", self.artifact_ids)
        if self.error is not None:
            payload.setdefault("error", self.error.model_dump(mode="json"))
        return payload

    @property
    def telemetry(self) -> dict[str, Any]:
        """Project execution metadata into the persisted result telemetry."""
        return {
            **self.execution_metadata,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "resource_usage": self.resource_usage,
        }


__all__ = [
    "AuthorizedExecutionRequest", "ExecutionBackendKind", "ExecutionResult",
    "ProducedFile",
]
