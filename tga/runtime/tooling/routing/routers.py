"""Four explicit business-semantic tool routers."""

from __future__ import annotations

from typing import Any, Callable

from tga.runtime.tooling.results import ExecutionError, RawExecutionResult


class ControlToolRouter:
    def __init__(self, handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]):
        self.handlers = handlers

    def execute(self, action) -> RawExecutionResult:
        handler = self.handlers.get(action.capability)
        if handler is None:
            return RawExecutionResult(
                action_id=action.id, status="blocked", output={}, artifact_ids=[],
                telemetry={"router": "control"},
                error=ExecutionError(
                    code="CONTROL_TOOL_NOT_IMPLEMENTED",
                    message=f"Control tool is reserved but not implemented: {action.capability}",
                ),
            )
        output = handler(action.normalized_arguments)
        error_payload = output.get("error") if isinstance(output.get("error"), dict) else None
        status = "succeeded" if output.get("ok") else str(output.get("status") or "blocked")
        if status not in {"succeeded", "failed", "blocked", "rejected", "cancelled"}:
            status = "blocked"
        return RawExecutionResult(
            action_id=action.id,
            status=status,
            output=output,
            artifact_ids=[str(item) for item in output.get("artifact_ids") or []],
            telemetry={"router": "control"},
            error=ExecutionError.model_validate(error_payload) if error_payload else None,
        )


class ResourceReadToolRouter:
    def __init__(self, adapter, handlers=None):
        self.adapter = adapter
        self.handlers = handlers or {}

    def execute(self, action) -> RawExecutionResult:
        handler = self.handlers.get(action.capability)
        if handler is not None:
            output = handler(action.normalized_arguments)
            return RawExecutionResult(
                action_id=action.id,
                status="succeeded" if output.get("ok") else "blocked",
                output=output,
                artifact_ids=[],
                telemetry={"router": "resource_read", "native": True},
            )
        return self.adapter.execute(action)


class ExecutionToolRouter(ResourceReadToolRouter):
    def __init__(self, adapter):
        super().__init__(adapter)


class RetrievalToolRouter:
    def __init__(self, handlers=None):
        self.handlers = handlers or {}

    def execute(self, action) -> RawExecutionResult:
        handler = self.handlers.get(action.capability)
        if handler is not None:
            output = handler(action.normalized_arguments)
            error_payload = output.get("error") if isinstance(output.get("error"), dict) else None
            return RawExecutionResult(
                action_id=action.id,
                status="succeeded" if output.get("ok") else str(output.get("status") or "blocked"),
                output=output,
                artifact_ids=[],
                telemetry={"router": "retrieval", "native": True},
                error=ExecutionError.model_validate(error_payload) if error_payload else None,
            )
        return RawExecutionResult(
            action_id=action.id, status="blocked", output={}, artifact_ids=[],
            telemetry={"router": "retrieval"},
            error=ExecutionError(
                code="RETRIEVAL_NOT_AVAILABLE",
                message="No Retrieval backend is configured for this Solver.",
            ),
        )


__all__ = [
    "ControlToolRouter", "ExecutionToolRouter", "ResourceReadToolRouter",
    "RetrievalToolRouter",
]
