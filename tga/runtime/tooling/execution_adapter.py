"""Execute one governed Action without owning its durable lifecycle."""

from __future__ import annotations

from typing import Any

from tga.contracts import ActionSpec
from tga.runtime.tooling.requests import AuthorizationDecision, GovernedAction
from tga.runtime.tooling.results import ExecutionError, RawExecutionResult


class ExecutionPipelineAdapter:
    def __init__(self, *, handlers) -> None:
        self.handlers = handlers

    def authorization(self, action: GovernedAction) -> AuthorizationDecision | None:
        """Apply the current MCP policy before any external I/O."""
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
        name = action.provider_tool_name
        execution = self._execution_action(action)
        if name.startswith("input_"):
            payload = self.handlers.inputs.execute_governed(execution)
        elif action.capability.startswith("mcp:"):
            payload = self.handlers.mcp.execute_governed(execution)
        else:
            payload = self.handlers.capability.execute_governed(execution)
        return self._raw(action, payload)

    def resume_approved(self, action: GovernedAction) -> RawExecutionResult:
        execution = self._execution_action(action)
        payload = (
            self.handlers.mcp.execute_governed(execution, approved=True)
            if action.capability.startswith("mcp:")
            else self.handlers.capability.execute_governed(execution)
        )
        return self._raw(action, payload)

    def _execution_action(self, action: GovernedAction) -> ActionSpec:
        metadata = action.execution_metadata
        kind = (
            "http" if action.capability == "http.request"
            else "workspace" if action.capability.startswith("workspace.")
            else "tool"
        )
        return ActionSpec(
            id=action.id,
            task_id=action.context.task_id,
            solver_id=action.context.solver_id,
            intent_id=action.context.intent_id,
            local_plan_step_id=action.context.local_plan_step_id,
            execution_policy_snapshot_id=action.context.execution_policy_snapshot_id,
            solver_tool_policy_snapshot_id=action.context.solver_tool_policy_snapshot_id,
            governed_action_id=action.id,
            kind=kind,
            capability=action.capability,
            target=action.resolved_target or action.capability,
            arguments=action.normalized_arguments,
            rationale=action.rationale,
            risk=action.risk,
            expected_outcome=action.expected_outcome,
            retry_reason=action.retry_reason or "",
            alternative_analysis=action.alternative_analysis or "",
            effect=action.effect,
            input_id=str(action.normalized_arguments.get("input_id") or "") or None,
            target_ref=str(action.normalized_arguments.get("input_id") or "") or None,
            actual_target=action.resolved_target,
            authorization={
                **action.authorization.model_dump(mode="json"),
                "tool_call_id": action.tool_call_id,
                "provider_tool_name": action.provider_tool_name,
                "mcp_provider_name": action.provider_tool_name,
                "mcp_server": metadata.get("mcp_server_id"),
                "mcp_method": metadata.get("mcp_method"),
                "catalog_version": metadata.get("mcp_catalog_version"),
            },
            provenance={
                "source": "governed_action",
                "governed_action_id": action.id,
            },
        )

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
                "adapter": "governed_execution",
            },
            error=ExecutionError.model_validate(error_payload) if error_payload else None,
        )


__all__ = ["ExecutionPipelineAdapter"]

