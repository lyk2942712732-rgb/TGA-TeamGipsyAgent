"""Mechanical bridge from governed Actions to the proven Phase-5 handlers."""

from __future__ import annotations

from typing import Any

from tga.runtime.tooling.requests import AuthorizationDecision, GovernedAction
from tga.runtime.tooling.results import ExecutionError, RawExecutionResult


class LegacyToolPipelineAdapter:
    def __init__(self, *, dispatcher, handlers) -> None:
        self.dispatcher = dispatcher
        self.handlers = handlers

    def authorization(self, action: GovernedAction) -> AuthorizationDecision | None:
        """Expose legacy MCP policy as a pre-I/O Gateway authorization check."""
        if not action.capability.startswith("mcp:"):
            return None
        route = self.handlers.state.mcp_snapshot.route(action.provider_tool_name)
        if route is None or self.handlers.state.mcp_manager.config is None:
            return AuthorizationDecision(
                allowed=False,
                code="MCP_ROUTE_UNAVAILABLE",
                reason="The MCP route is no longer available.",
                policy_snapshot_ids=action.authorization.policy_snapshot_ids,
            )
        server = self.handlers.state.mcp_manager.config.servers.get(route.server_id)
        if server is None:
            return AuthorizationDecision(
                allowed=False,
                code="MCP_SERVER_UNAVAILABLE",
                reason="The MCP server is no longer configured.",
                policy_snapshot_ids=action.authorization.policy_snapshot_ids,
            )
        try:
            denial = self.handlers.state.mcp_manager.policy.authorize(
                task=self.handlers.state.task,
                server=server,
                route=route,
                arguments=action.normalized_arguments,
            )
        except Exception as exc:
            denial = f"MCP_POLICY_ERROR:{str(exc)[:500]}"
        if not denial:
            return None
        if denial.startswith("APPROVAL_REQUIRED:"):
            return AuthorizationDecision(
                allowed=True,
                code="APPROVAL_REQUIRED",
                reason=denial,
                requires_approval=True,
                policy_snapshot_ids=action.authorization.policy_snapshot_ids,
            )
        return AuthorizationDecision(
            allowed=False,
            code="MCP_POLICY_DENIED",
            reason=denial,
            policy_snapshot_ids=action.authorization.policy_snapshot_ids,
        )

    def execute(self, action: GovernedAction) -> RawExecutionResult:
        intent = action.model_dump(mode="json")["context"]
        governance: dict[str, Any] = {
            "rationale": action.rationale,
            "expected_outcome": action.expected_outcome,
            "retry_reason": action.retry_reason or "",
            "alternative_analysis": action.alternative_analysis or "",
            "effect": action.effect.model_dump(mode="json"),
            "_host_action_context": intent,
            "_governed_action_id": action.id,
        }
        call = {
            "id": action.tool_call_id,
            "function": {
                "name": action.provider_tool_name,
                "arguments": {**action.normalized_arguments, "_tga": governance},
            },
        }
        payload = self.dispatcher.dispatch(task=self.handlers.state.task, call=call)
        return self._raw(action, payload)

    def resume_approved(self, *, governed_action: GovernedAction, legacy_action) -> RawExecutionResult:
        payload = (
            self.handlers.mcp.execute_approved(legacy_action)
            if legacy_action.authorization.get("mcp_server")
            else self.handlers.capability.execute_approved(legacy_action)
        )
        return self._raw(governed_action, payload)

    @staticmethod
    def _raw(action: GovernedAction, payload: dict[str, Any]) -> RawExecutionResult:
        artifacts = [str(item) for item in payload.get("artifact_ids") or []]
        if payload.get("artifact_id") and str(payload["artifact_id"]) not in artifacts:
            artifacts.append(str(payload["artifact_id"]))
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else None
        status = str(payload.get("status") or ("succeeded" if payload.get("ok") else "blocked"))
        allowed = {
            "pending_approval", "succeeded", "failed", "blocked", "rejected",
            "expired", "cancelled",
        }
        if status not in allowed:
            status = "succeeded" if payload.get("ok") else "blocked"
        return RawExecutionResult(
            action_id=action.id,
            status=status,
            output=payload,
            artifact_ids=artifacts,
            telemetry={
                "adapter": "legacy_tool_pipeline",
                "legacy_action_id": payload.get("action_id"),
            },
            error=ExecutionError.model_validate(error_payload) if error_payload else None,
        )


__all__ = ["LegacyToolPipelineAdapter"]
