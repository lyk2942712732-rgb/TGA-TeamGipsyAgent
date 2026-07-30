"""The only schema-v6 entry point for model-initiated tool calls."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from typing import Callable
from urllib.parse import urljoin

from tga.domain.governance.models import ActionEffect
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.runtime.scheduling.budgets import NetworkBudgetLimiter
from tga.runtime.tooling.governance import (
    BudgetService,
    IdempotencyService,
    ResourceLockService,
    SemanticRepeatGuard,
)
from tga.runtime.tooling.lifecycle import GovernedActionService
from tga.runtime.tooling.requests import (
    ApprovalRequest,
    AuthorizationDecision,
    GovernedAction,
    ToolRequest,
)
from tga.runtime.tooling.results import (
    ExecutionError,
    RawExecutionResult,
    ToolGatewayResult,
    ToolObservation,
)
from tga.runtime.tooling.routing.routers import (
    ControlToolRouter,
    ExecutionToolRouter,
    ResourceReadToolRouter,
    RetrievalToolRouter,
)
from tga.tools.tool_policy import is_allowed
from tga.tools.mcp_policy import validate_json_schema


AUTHORITATIVE_ARGUMENTS = {
    "task_id", "solver_id", "intent_id", "policy_snapshot_id",
    "execution_policy_snapshot_id", "tool_policy_snapshot_id",
    "solver_tool_policy_snapshot_id", "parent_solver_id", "global_plan_version",
    "strategy_card_id", "strategy_step_id", "local_plan_step_id",
}


class ToolGovernanceGateway:
    def __init__(
        self,
        *,
        task,
        manifest,
        repository,
        legacy_adapter,
        control_handlers: dict[str, Any] | None = None,
        resource_handlers: dict[str, Any] | None = None,
        retrieval_handlers: dict[str, Any] | None = None,
        event_repository=None,
        allowed_resource_ids: tuple[str, ...] | None = None,
        lease_validator: Callable[[], bool] | None = None,
        artifact_result_handler: Callable[[RawExecutionResult], None] | None = None,
    ) -> None:
        self.task = task
        self.manifest = manifest
        self.repository = repository
        self.legacy_adapter = legacy_adapter
        self.events = event_repository
        self.allowed_resource_ids = allowed_resource_ids
        self.lease_validator = lease_validator
        self.artifact_result_handler = artifact_result_handler
        self.actions = GovernedActionService(repository)
        self.semantic_repeat = SemanticRepeatGuard(repository)
        self.idempotency = IdempotencyService(repository)
        self.locks = ResourceLockService(repository)
        self.budgets = BudgetService(repository)
        self.network = NetworkBudgetLimiter(repository)
        self.routers = {
            "control": ControlToolRouter(control_handlers or {}),
            "resource_read": ResourceReadToolRouter(
                legacy_adapter, resource_handlers or {}
            ),
            "execution": ExecutionToolRouter(legacy_adapter),
            "retrieval": RetrievalToolRouter(retrieval_handlers or {}),
        }

    def resume_approved(self, legacy_action) -> ToolGatewayResult:
        action = self._governed_for_legacy(legacy_action)
        if action is None:
            return self._error(
                "GOVERNED_ACTION_NOT_FOUND",
                "Approved legacy Action has no governed Action ownership.",
            )
        current = self.repository.get_action(action.id)
        if current is None or current["status"] != "pending_approval":
            return self._error(
                "APPROVAL_ACTION_STATE_INVALID",
                "Approval can only resume its original pending Action.",
                action_id=action.id,
            )
        self.actions.transition(action.id, "approved", expected_status="pending_approval")
        reservation = None
        network_permit = None
        locked = False
        try:
            reservation = self.budgets.reserve(
                action,
                tool_calls=1,
                artifacts=self._artifact_reservation(action),
            )
            if action.resource_lock_key:
                locked = self.locks.acquire(action, ttl_seconds=120)
                if not locked:
                    self.budgets.release(reservation.id)
                    self.actions.transition(action.id, "cancelled", expected_status="approved")
                    return self._error(
                        "RESOURCE_LOCK_CONFLICT",
                        "Approved Action could not reacquire its resource lock.",
                        action_id=action.id,
                    )
            if action.capability == "http.request":
                network_permit = self.network.acquire(
                    idempotency_key=action.id,
                    task_id=action.context.task_id,
                    solver_id=action.context.solver_id,
                    intent_id=action.context.intent_id,
                )
            self.actions.transition(action.id, "queued", expected_status="approved")
            self.actions.transition(action.id, "running", expected_status="queued")
            try:
                raw = self.legacy_adapter.resume_approved(
                    governed_action=action, legacy_action=legacy_action
                )
            except Exception as exc:
                raw = self._execution_failure(action, exc)
            if self.lease_validator is not None and not self.lease_validator():
                self.budgets.release(reservation.id)
                self.actions.transition(
                    action.id, "cancelled", expected_status="running"
                )
                return self._error(
                    "RUNNER_LEASE_LOST",
                    "Execution completed after its Solver runner lease was lost; the result was not submitted.",
                    action_id=action.id,
                )
            terminal = raw.status if raw.status in {"succeeded", "failed", "blocked", "cancelled"} else "blocked"
            self.actions.persist_result(action.id, raw)
            self.actions.transition(action.id, terminal, expected_status="running")
            self.budgets.settle(reservation.id, artifacts=len(raw.artifact_ids))
            self._handle_artifact_result(raw)
            result = self._map(action, raw)
            self._publish(action, raw, result.observation)
            return result
        except PersistenceConflict as exc:
            current = self.repository.get_action(action.id)
            if current and current["status"] == "approved":
                self.actions.transition(
                    action.id, "cancelled", expected_status="approved"
                )
            if reservation is not None:
                self.budgets.release(reservation.id)
            return self._error(
                "GOVERNANCE_RESERVATION_DENIED", str(exc), action_id=action.id
            )
        finally:
            if network_permit is not None:
                self.network.release(network_permit)
            if locked:
                self.locks.release(action)

    def resolve_without_execution(self, legacy_action, *, status: str, payload: dict[str, Any]) -> ToolGatewayResult:
        action = self._governed_for_legacy(legacy_action)
        if action is None:
            return ToolGatewayResult(status=status, model_payload=payload)
        current = self.repository.get_action(action.id)
        terminal = "expired" if status == "expired" else "rejected"
        if current is not None and current["status"] == "pending_approval":
            raw = RawExecutionResult(
                action_id=action.id,
                status=terminal,
                output=payload,
                artifact_ids=[],
                telemetry={"approval_resolution": terminal},
                error=ExecutionError.model_validate(payload["error"])
                if isinstance(payload.get("error"), dict) else None,
            )
            self.actions.persist_result(action.id, raw)
            self.actions.transition(action.id, terminal, expected_status="pending_approval")
        return ToolGatewayResult(
            action_id=action.id,
            status=terminal,
            error=ExecutionError.model_validate(payload["error"])
            if isinstance(payload.get("error"), dict) else None,
            model_payload=payload,
        )

    def _governed_for_legacy(self, legacy_action) -> GovernedAction | None:
        if not legacy_action.governed_action_id:
            return None
        item = self.repository.get_action(legacy_action.governed_action_id)
        return GovernedAction.model_validate(item["payload"]) if item else None

    def handle(self, request: ToolRequest) -> ToolGatewayResult:
        early = self._preflight(request)
        if early is not None:
            return early
        definition = self.manifest.get(request.provider_tool_name)
        assert definition is not None
        now = utc_now()
        target = self._resolve_target(definition.capability, request.arguments, request.action_context)
        risk = self._risk(definition, request.arguments)
        effect = request.model_intent.proposed_effect or ActionEffect()
        authorization = self._authorize(
            definition=definition,
            request=request,
            target=target,
            risk=risk,
            effect=effect,
        )
        fingerprint = self._fingerprint(request, definition.capability, target)
        high_impact = self._high_impact(risk, effect, definition.capability, request.arguments)
        action = GovernedAction(
            id=self._action_id(request),
            context=request.action_context,
            provider_tool_name=request.provider_tool_name,
            tool_call_id=request.tool_call_id,
            tool_class=definition.tool_class,
            capability=definition.capability,
            normalized_arguments=request.arguments,
            resolved_target=target,
            rationale=request.model_intent.rationale,
            expected_outcome=request.model_intent.expected_outcome,
            retry_reason=request.model_intent.retry_reason,
            alternative_analysis=request.model_intent.alternative_analysis,
            risk=risk,
            effect=effect,
            authorization=authorization,
            attempt=request.action_context.attempt,
            idempotency_key=self._idempotency_key(request, definition.capability, target)
            if high_impact else None,
            semantic_fingerprint=fingerprint,
            resource_lock_key=self._lock_key(
                definition.capability, target, request.arguments
            ) if high_impact else None,
            status="proposed",
            created_at=now,
            updated_at=now,
        )
        adapter_authorization = getattr(self.legacy_adapter, "authorization", None)
        if callable(adapter_authorization):
            replacement = adapter_authorization(action)
            if replacement is not None:
                if (
                    replacement.requires_approval
                    and (effect == ActionEffect() or not request.model_intent.alternative_analysis)
                ):
                    replacement = AuthorizationDecision(
                        allowed=False,
                        code="HIGH_IMPACT_EFFECT_REQUIRED",
                        reason="High-impact Actions require a proposed effect and alternative analysis.",
                        policy_snapshot_ids=replacement.policy_snapshot_ids,
                    )
                action = action.model_copy(update={"authorization": replacement})
                authorization = replacement
        existing = self.repository.find_by_tool_call(
            request.action_context.task_id,
            request.action_context.solver_id,
            request.tool_call_id,
        )
        if existing is not None:
            if existing.get("result"):
                return self._result_from_persisted(existing)
            return self._error(
                "ACTION_ALREADY_IN_PROGRESS", "This tool call already has a durable Action.",
                action_id=str(existing["id"]),
            )
        self.actions.propose(action)
        if not authorization.allowed:
            self.actions.transition(action.id, "denied", expected_status="proposed")
            return self._error(
                authorization.code or "POLICY_DENIED", authorization.reason,
                action_id=action.id,
            )
        self.actions.transition(action.id, "validated", expected_status="proposed")

        repeat = self.semantic_repeat.check(action)
        prior_idempotency = self.idempotency.lookup(action)
        if prior_idempotency is not None:
            self.actions.transition(action.id, "cancelled", expected_status="validated")
            if prior_idempotency.result:
                return self._result_from_raw(prior_idempotency.result, idempotent_replay=True)
            return self._error(
                "IDEMPOTENCY_IN_PROGRESS",
                "An equivalent high-impact operation is already reserved.",
                action_id=prior_idempotency.action_id,
            )
        if repeat.requires_retry_reason and not request.model_intent.retry_reason:
            self.actions.transition(action.id, "denied", expected_status="validated")
            return self._error(
                "SEMANTIC_REPEAT_REQUIRES_REASON",
                "A semantically repeated action requires a new retry reason.",
                action_id=action.id,
                extra={"previous_action_id": repeat.previous_action_id},
            )
        idempotency = self.idempotency.reserve(action)
        if not idempotency.created:
            self.actions.transition(action.id, "cancelled", expected_status="validated")
            if idempotency.result:
                return self._result_from_raw(idempotency.result, idempotent_replay=True)
            return self._error(
                "IDEMPOTENCY_IN_PROGRESS",
                "An equivalent high-impact operation is already reserved.",
                action_id=idempotency.action_id,
            )

        if authorization.requires_approval:
            raw = self._execute_router(action)
            if raw.status != "pending_approval":
                self.actions.transition(action.id, "denied", expected_status="validated")
                return self._error(
                    "APPROVAL_ADAPTER_CONTRACT_VIOLATION",
                    "The execution adapter did not stop before approval.",
                    action_id=action.id,
                )
            self.actions.transition(action.id, "pending_approval", expected_status="validated")
            self._save_approval(action, raw)
            return self._map(action, raw)

        reservation = None
        network_permit = None
        locked = False
        try:
            reservation = self.budgets.reserve(
                action,
                tool_calls=1,
                artifacts=self._artifact_reservation(action),
            )
            if action.resource_lock_key:
                locked = self.locks.acquire(action, ttl_seconds=120)
                if not locked:
                    self.budgets.release(reservation.id)
                    self.actions.transition(action.id, "denied", expected_status="validated")
                    return self._error(
                        "RESOURCE_LOCK_CONFLICT",
                        "Another Action owns the conflicting resource lock.",
                        action_id=action.id,
                    )
            if action.capability == "http.request":
                network_permit = self.network.acquire(
                    idempotency_key=action.id,
                    task_id=action.context.task_id,
                    solver_id=action.context.solver_id,
                    intent_id=action.context.intent_id,
                )
            self.actions.transition(action.id, "queued", expected_status="validated")
            self.actions.transition(action.id, "running", expected_status="queued")
            raw = self._execute_router(action)
            if self.lease_validator is not None and not self.lease_validator():
                self.budgets.release(reservation.id)
                reservation = None
                self.actions.transition(
                    action.id, "cancelled", expected_status="running"
                )
                return self._error(
                    "RUNNER_LEASE_LOST",
                    "Execution completed after its Solver runner lease was lost; the result was not submitted.",
                    action_id=action.id,
                )
            terminal = raw.status if raw.status in {"succeeded", "failed", "blocked", "cancelled"} else "blocked"
            self.actions.persist_result(action.id, raw)
            self.actions.transition(action.id, terminal, expected_status="running")
            self.budgets.settle(reservation.id, artifacts=len(raw.artifact_ids))
            self._handle_artifact_result(raw)
            result = self._map(action, raw)
            self._publish(action, raw, result.observation)
            return result
        except PersistenceConflict as exc:
            current = self.repository.get_action(action.id)
            if current and current["status"] == "validated":
                self.actions.transition(action.id, "denied", expected_status="validated")
            if reservation is not None:
                self.budgets.release(reservation.id)
            return self._error("GOVERNANCE_RESERVATION_DENIED", str(exc), action_id=action.id)
        finally:
            if network_permit is not None:
                self.network.release(network_permit)
            if locked:
                self.locks.release(action)

    def _preflight(self, request: ToolRequest) -> ToolGatewayResult | None:
        if self.lease_validator is not None and not self.lease_validator():
            return self._error(
                "RUNNER_LEASE_LOST",
                "The Solver runner no longer owns the durable lease.",
            )
        if request.action_context.task_id != self.task.id:
            return self._error("ACTION_CONTEXT_TASK_MISMATCH", "Host ActionContext does not own this task.")
        if (
            request.action_context.solver_id != self.manifest.solver_id
            or request.action_context.intent_id != self.manifest.intent_id
        ):
            return self._error("ACTION_CONTEXT_ASSIGNMENT_MISMATCH", "Host ActionContext does not match the manifest assignment.")
        if self._contains_authoritative_argument(request.arguments):
            return self._error(
                "AUTHORITATIVE_ARGUMENT_FORBIDDEN",
                "Task, Solver, Intent, Plan and policy identities are host-owned.",
            )
        definition = self.manifest.get(request.provider_tool_name)
        if definition is None:
            return self._error("TOOL_NOT_IN_MANIFEST", "The current Solver manifest does not expose this tool.")
        if definition.tool_class == "resource_read":
            try:
                requested = int(request.arguments.get("limit") or 0)
            except (TypeError, ValueError):
                return self._error(
                    "RESOURCE_READ_LIMIT_INVALID",
                    "Resource read limit must be an integer.",
                )
            if definition.max_read_chars is not None and requested > definition.max_read_chars:
                return self._error(
                    "RESOURCE_READ_LIMIT_EXCEEDED",
                    f"Resource read limit is {definition.max_read_chars} characters.",
                )
        argument_error = validate_json_schema(definition.parameters, request.arguments)
        if argument_error:
            return self._error("INVALID_TOOL_ARGUMENTS", argument_error)
        scope_error = self._resource_scope_error(definition.capability, request.arguments)
        if scope_error:
            return self._error("RESOURCE_NOT_OWNED", scope_error)
        return None

    def _handle_artifact_result(self, raw: RawExecutionResult) -> None:
        if self.artifact_result_handler is None or not raw.artifact_ids:
            return
        try:
            self.artifact_result_handler(raw)
        except Exception:
            # Retrieval indexing is a derived projection and cannot rewrite a
            # completed authoritative tool result.
            return

    def _resource_scope_error(
        self, capability: str, arguments: dict[str, Any]
    ) -> str | None:
        input_id = str(arguments.get("input_id") or "")
        if input_id and capability.startswith("input_"):
            owned = {
                item.id for item in self.task.session_input.files
                if self.allowed_resource_ids is None
                or item.id in self.allowed_resource_ids
            }
            if input_id not in owned:
                return "The requested input is not owned by this Task."
        artifact_id = str(arguments.get("artifact_id") or "")
        if artifact_id:
            if (
                self.allowed_resource_ids is not None
                and artifact_id not in self.allowed_resource_ids
            ):
                return "The requested Artifact is outside this SolverAssignment."
            row = self.repository.conn.execute(
                "SELECT 1 FROM artifacts WHERE id=? AND task_id=?",
                (artifact_id, self.task.id),
            ).fetchone()
            if row is None:
                return "The requested Artifact is not owned by this Task."
        if capability.startswith("workspace."):
            value = str(
                arguments.get("relative_path")
                or arguments.get("script_path")
                or ""
            ).replace("\\", "/")
            path = PurePosixPath(value)
            if PureWindowsPath(value).is_absolute() or path.is_absolute() or ".." in path.parts:
                return "Workspace paths must remain relative to the owning Solver workspace."
        return None

    def _execute_router(self, action: GovernedAction) -> RawExecutionResult:
        try:
            return self.routers[action.tool_class].execute(action)
        except Exception as exc:
            return self._execution_failure(action, exc)

    @staticmethod
    def _execution_failure(action: GovernedAction, exc: Exception) -> RawExecutionResult:
        error = ExecutionError(
            code="EXECUTION_ADAPTER_ERROR",
            message=f"Tool adapter failed: {str(exc)[:800]}",
            retryable=False,
        )
        return RawExecutionResult(
            action_id=action.id,
            status="failed",
            output={
                "ok": False,
                "status": "failed",
                "error": error.model_dump(mode="json"),
            },
            artifact_ids=[],
            telemetry={"router": action.tool_class, "adapter_exception": True},
            error=error,
        )

    @staticmethod
    def _artifact_reservation(action: GovernedAction) -> int:
        # Current legacy execution/resource handlers publish at most one
        # primary Artifact per call. Reserve it before I/O; settlement lowers
        # the reservation to zero when a call produces no Artifact.
        return 1 if action.tool_class in {"resource_read", "execution"} else 0

    @staticmethod
    def _contains_authoritative_argument(arguments: dict[str, Any]) -> bool:
        def contains(value: Any) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).casefold() in AUTHORITATIVE_ARGUMENTS or contains(item)
                    for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(contains(item) for item in value)
            return False

        return contains(arguments)

    def _authorize(self, *, definition, request, target: str | None, risk: str, effect: ActionEffect) -> AuthorizationDecision:
        snapshots = (
            request.action_context.execution_policy_snapshot_id,
            request.action_context.solver_tool_policy_snapshot_id,
        )
        if definition.tool_class != "execution":
            return AuthorizationDecision(
                allowed=True, reason="manifest and ownership checks passed",
                policy_snapshot_ids=snapshots,
            )
        decision = is_allowed(
            tool=definition.capability,
            target=target or self.task.id,
            task=self.task,
            risk=risk,
            action=str(request.arguments.get("method") or definition.capability),
            sandboxed=self.task.execution_policy.local_compute.mode == "isolated",
        )
        if decision.code == "APPROVAL_REQUIRED":
            if effect == ActionEffect() or not request.model_intent.alternative_analysis:
                return AuthorizationDecision(
                    allowed=False,
                    code="HIGH_IMPACT_EFFECT_REQUIRED",
                    reason="High-impact Actions require a proposed effect and alternative analysis.",
                    policy_snapshot_ids=snapshots,
                )
            return AuthorizationDecision(
                allowed=True, code=decision.code, reason=decision.reason,
                requires_approval=True, policy_snapshot_ids=snapshots,
            )
        return AuthorizationDecision(
            allowed=decision.allowed,
            code=decision.code,
            reason=decision.reason,
            policy_snapshot_ids=snapshots,
        )

    def _save_approval(self, action: GovernedAction, raw: RawExecutionResult) -> None:
        legacy_action_id = str(raw.telemetry.get("legacy_action_id") or "")
        expires_at = ""
        if legacy_action_id:
            expires_at = self.repository.legacy_approval_expiry(legacy_action_id) or ""
        if not expires_at:
            expires_at = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
        now = utc_now()
        approval = ApprovalRequest(
            id=f"approval_{action.id}",
            task_id=action.context.task_id,
            solver_id=action.context.solver_id,
            intent_id=action.context.intent_id,
            action_id=legacy_action_id or action.id,
            governed_action_id=action.id,
            reason=action.authorization.reason,
            risk=action.risk,
            effect=action.effect,
            alternatives=(action.alternative_analysis,) if action.alternative_analysis else (),
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self.repository.save_approval(approval)
        if self.events is not None:
            self.events.append_agent_event(
                action.context.task_id,
                "APPROVAL_REQUESTED",
                {
                    "approval_id": approval.id,
                    "action_id": approval.action_id,
                    "governed_action_id": approval.governed_action_id,
                    "risk": approval.risk,
                    "reason": approval.reason,
                    "deadline": approval.expires_at,
                },
                solver_id=approval.solver_id,
                intent_id=approval.intent_id,
            )

    def _publish(self, action, raw, observation) -> None:
        if self.events is None:
            return
        self.events.append_agent_event(
            action.context.task_id,
            "TOOL_OBSERVATION_PUBLISHED",
            {
                "governed_action_id": action.id,
                "tool_class": action.tool_class,
                "capability": action.capability,
                "status": raw.status,
                "artifact_ids": raw.artifact_ids,
                "observation": observation.model_dump(mode="json") if observation else None,
            },
            solver_id=action.context.solver_id,
        )

    def _resolve_target(self, capability: str, arguments: dict[str, Any], context) -> str | None:
        if capability == "http.request":
            requested_url = str(arguments.get("url") or "")
            if requested_url:
                return requested_url
            path = str(arguments.get("path") or "")
            return urljoin(self.task.task_entry_url or "", path) or None
        if capability.startswith("workspace.") or capability == "input_materialize":
            relative = str(arguments.get("relative_path") or arguments.get("script_path") or "workspace")
            return f"workspace:{context.solver_id}:{relative}"
        if capability.startswith("mcp:") or capability == "mcp.gateway":
            return f"mcp:{capability}:{arguments.get('resource_key') or arguments.get('tool') or 'call'}"
        return str(arguments.get("input_id") or arguments.get("artifact_id") or capability)

    @staticmethod
    def _risk(definition, arguments: dict[str, Any]) -> str:
        if definition.capability == "http.request":
            method = str(arguments.get("method") or "GET").upper()
            return "destructive" if method == "DELETE" else "active" if method not in {"GET", "HEAD"} else "passive"
        return definition.risk if definition.risk in {"passive", "active", "destructive"} else "active"

    @staticmethod
    def _high_impact(risk, effect, capability, arguments) -> bool:
        return (
            risk == "destructive"
            or effect.persistence == "persistent"
            or capability in {"workspace.write", "input_materialize"}
            or capability == "http.request" and str(arguments.get("method") or "GET").upper() not in {"GET", "HEAD"}
        )

    @staticmethod
    def _fingerprint(request, capability: str, target: str | None) -> str:
        encoded = json.dumps(
            [request.action_context.task_id, request.action_context.solver_id,
             request.action_context.intent_id, capability, target, request.arguments],
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _idempotency_key(request, capability: str, target: str | None) -> str:
        encoded = json.dumps(
            [request.action_context.task_id, request.action_context.intent_id,
             request.action_context.solver_id, capability, target, request.arguments,
             request.action_context.attempt],
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _lock_key(capability: str, target: str | None, arguments: dict[str, Any]) -> str:
        if capability.startswith("mcp:") or capability == "mcp.gateway":
            return target or f"mcp:{capability}"
        return target or f"capability:{capability}"

    @staticmethod
    def _action_id(request: ToolRequest) -> str:
        digest = hashlib.sha256(
            f"{request.action_context.task_id}\0{request.action_context.solver_id}\0{request.tool_call_id}".encode()
        ).hexdigest()[:24]
        return f"governed_{digest}"

    @staticmethod
    def _map(action: GovernedAction, raw: RawExecutionResult) -> ToolGatewayResult:
        output = dict(raw.output)
        summary = str(output.get("summary") or "")[:8_000]
        observation = ToolObservation(
            action_id=action.id,
            summary=summary,
            deterministic_facts=[str(item)[:8_000] for item in output.get("facts") or []][:256],
            leads=[str(item)[:8_000] for item in output.get("leads") or []][:256],
            candidate_evidence_claim_ids=[str(item) for item in output.get("candidate_evidence_claim_ids") or []],
            candidate_knowledge_ids=[str(item) for item in output.get("candidate_knowledge_ids") or []],
        )
        bounded = output
        if len(json.dumps(bounded, ensure_ascii=False, default=str)) > 64_000:
            bounded = {
                "ok": raw.status == "succeeded", "status": raw.status,
                "summary": summary[:2_000], "artifact_ids": raw.artifact_ids,
                "truncated": True,
            }
        return ToolGatewayResult(
            action_id=action.id,
            status=raw.status,
            observation=observation,
            artifact_ids=raw.artifact_ids,
            error=raw.error,
            model_payload=bounded,
        )

    @staticmethod
    def _error(code: str, message: str, *, action_id: str | None = None, extra=None):
        error = ExecutionError(code=code, message=message, retryable=False)
        payload = {
            "ok": False, "status": "blocked", "error": error.model_dump(mode="json"),
            **(extra or {}),
        }
        return ToolGatewayResult(
            action_id=action_id, status="blocked", error=error, model_payload=payload,
        )

    @staticmethod
    def _result_from_persisted(existing: dict[str, Any]) -> ToolGatewayResult:
        return ToolGovernanceGateway._result_from_raw(existing["result"], idempotent_replay=True)

    @staticmethod
    def _result_from_raw(raw: dict[str, Any], *, idempotent_replay: bool) -> ToolGatewayResult:
        result = RawExecutionResult.model_validate(raw)
        return ToolGatewayResult(
            action_id=result.action_id,
            status=result.status,
            artifact_ids=result.artifact_ids,
            error=result.error,
            model_payload=result.output,
            idempotent_replay=idempotent_replay,
        )


__all__ = ["ToolGovernanceGateway"]
