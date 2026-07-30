"""Concrete governed tool handlers used by the native ReAct runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from tga.contracts import ActionEffect, ActionResult, ActionSpec, ArtifactRecord, TGAError, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.inputs import SessionWorkspace, task_artifact_root
from tga.runtime.completion_validators import CompletionValidationContext, FinishSubmission, validator_for
from tga.runtime.coordinator import SessionCoordinator, SessionOutcome
from tga.runtime.handler_services import ActionRecorder, ArtifactService, ObserverExecutionCoordinator, StrategyResolver
from tga.runtime.tool_handlers.plan_knowledge import PlanKnowledgeHandler
from tga.runtime.tool_handlers.task_completion import TaskCompletionHandler
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator
from tga.runtime.observer import DeterministicObserver, ObserverCoordinator
from tga.runtime.strategy import StrategyService
from tga.tools.mcp_gateway import MCPGateway, TGA_MCP_TOOL
from tga.tools.mcp_manager import MCPCallOutcome, MCPExecutionError, MCPManager
from tga.tools.mcp_policy import redact_sensitive
from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPToolRoute
from tga.tools.tool_policy import is_allowed


def _origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def safe_model_content(content: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "[code omitted from event; retained in audit transcript]", content)
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|secret|password|api[_-]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return re.sub(r"(https?://[^\s?#]+)\?[^\s]+", r"\1?[query omitted]", text)[:2000]


def safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if re.search(r"authorization|cookie|token|secret|password|api[_-]?key", str(key), re.IGNORECASE):
            safe[key] = "[REDACTED]"
        elif key == "body":
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="replace")
            safe[key] = {"present": value is not None, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()[:16]}
        elif key in {"headers", "query"} and isinstance(value, dict):
            safe[key] = {
                name: "[REDACTED]" if re.search(r"authorization|cookie|token|secret|key|password", str(name), re.IGNORECASE) else str(item)[:200]
                for name, item in value.items()
            }
        elif key in {"source", "content", "command", "stdin"}:
            text = str(value)
            safe[key] = {"present": bool(text), "chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]}
        elif key in {"summary", "claims", "coverage", "limitations", "flag"}:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="replace")
            safe[key] = {"present": value is not None and value != "" and value != [], "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()[:16]}
        else:
            safe[key] = value
    return safe


def _effect_from_governance(governance: dict[str, Any]) -> ActionEffect:
    raw = governance.get("effect")
    if raw is None:
        return ActionEffect()
    if not isinstance(raw, dict):
        raise ValueError("_tga.effect must be an object")
    return ActionEffect.model_validate(raw)


def _host_action_fields(governance: dict[str, Any]) -> dict[str, Any]:
    raw = governance.get("_host_action_context")
    context = raw if isinstance(raw, dict) else {}
    return {
        "intent_id": context.get("intent_id"),
        "local_plan_step_id": context.get("local_plan_step_id"),
        "execution_policy_snapshot_id": context.get("execution_policy_snapshot_id"),
        "solver_tool_policy_snapshot_id": context.get("solver_tool_policy_snapshot_id"),
        "governed_action_id": governance.get("_governed_action_id"),
    }


def safe_tool_call_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            raw = str(value or "")
            return {"present": bool(raw), "chars": len(raw), "sha256": hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]}
    return safe_arguments(parsed) if isinstance(parsed, dict) else {"type": type(parsed).__name__}


def lifecycle_event_payload(
    task: TGATask, solver_id: str, *, turn: int, code: str, missing: list[str],
    evidence_artifact_ids: list[str], terminal: bool, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task.id, "solver_id": solver_id, "mode": task.mode, "validator_code": code,
        "missing": [safe_model_content(item) for item in missing[:32]],
        "evidence_artifact_ids": list(dict.fromkeys(evidence_artifact_ids))[:64],
        "turn": turn, "terminal": terminal, **(extra or {}),
    }


class HandlerState:
    def __init__(
        self, *, task: TGATask, store: EvidenceStore, run_root: Path, client: Any, executor: Any,
        solver_id: str, workspace: Path, mcp_manager: MCPManager, mcp_snapshot: MCPCatalogSnapshot,
        registry: Any, tool_by_name: dict[str, str], remote_flag_verifier: Any | None = None,
        allowed_resource_ids: tuple[str, ...] | None = None,
    ) -> None:
        self.task = task
        self.store = store
        self.run_root = run_root
        self.client = client
        self.executor = executor
        self.solver_id = solver_id
        self.workspace = workspace
        self.mcp_manager = mcp_manager
        self.mcp_snapshot = mcp_snapshot
        self.registry = registry
        self.tool_by_name = tool_by_name
        self.remote_flag_verifier = remote_flag_verifier
        self.allowed_resource_ids = allowed_resource_ids
        self.strategies = StrategyService(store)
        self.observer = ObserverCoordinator(observer=DeterministicObserver(), store=store, cooldown_seconds=0)
        self.observer_directive = ""
        self.artifact_retrievals = 0
        self.last_finish_rejection: dict[str, Any] | None = None
        self.terminal_outcome: SessionOutcome | None = None
        self.last_artifact_id = self._latest_artifact_id()
        self.plan_knowledge = PlanKnowledgeHandler(self)

    def _latest_artifact_id(self) -> str | None:
        artifacts = self.store.list_artifacts(self.task.id)
        return artifacts[-1].id if artifacts else None

    def _first_flag(self, text: str) -> str | None:
        if not text or not self.task.flag_format:
            return None
        try:
            match = re.search(self.task.flag_format, text)
        except re.error:
            return None
        return match.group(0) if match else None

    def close(self) -> None:
        self.observer.close()


class HandlerRuntime:
    """Base for concrete handlers sharing only durable per-session state."""

    def __init__(self, state: HandlerState) -> None:
        self.state = state
        for name in (
            "task", "store", "run_root", "client", "executor", "solver_id", "workspace",
            "mcp_manager", "registry", "tool_by_name", "remote_flag_verifier",
            "strategies", "observer",
            "plan_knowledge",
        ):
            setattr(self, name, getattr(state, name))

    @property
    def mcp_snapshot(self) -> MCPCatalogSnapshot:
        return self.state.mcp_snapshot

    @property
    def observer_directive(self) -> str:
        return self.state.observer_directive

    @observer_directive.setter
    def observer_directive(self, value: str) -> None:
        self.state.observer_directive = value

    @property
    def artifact_retrievals(self) -> int:
        return self.state.artifact_retrievals

    @artifact_retrievals.setter
    def artifact_retrievals(self, value: int) -> None:
        self.state.artifact_retrievals = value

    @property
    def last_finish_rejection(self) -> dict[str, Any] | None:
        return self.state.last_finish_rejection

    @last_finish_rejection.setter
    def last_finish_rejection(self, value: dict[str, Any] | None) -> None:
        self.state.last_finish_rejection = value

    @property
    def terminal_outcome(self) -> SessionOutcome | None:
        return self.state.terminal_outcome

    @terminal_outcome.setter
    def terminal_outcome(self, value: SessionOutcome | None) -> None:
        self.state.terminal_outcome = value

    @property
    def last_artifact_id(self) -> str | None:
        return self.state.last_artifact_id

    @last_artifact_id.setter
    def last_artifact_id(self, value: str | None) -> None:
        self.state.last_artifact_id = value

    _first_flag = HandlerState._first_flag
    _safe_arguments = staticmethod(safe_arguments)
    _safe_model_content = staticmethod(safe_model_content)

    def _lifecycle_event_payload(self, **kwargs: Any) -> dict[str, Any]:
        return lifecycle_event_payload(self.task, self.solver_id, **kwargs)

    @staticmethod
    def _execution_location(capability: str) -> str:
        if capability == "artifact.inspect":
            return "Artifact Store"
        if capability.startswith("workspace."):
            return "Session Workspace"
        if capability == "http.request":
            return "Authorized HTTP Target"
        return "TGA Process"


class LegacyCompletionService(HandlerRuntime):
    def __init__(self, state: HandlerState, artifacts: ArtifactService) -> None:
        super().__init__(state)
        self.artifacts = artifacts

    def handle(self, *, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._handle_finish_submission(arguments)

    def _handle_finish_submission(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self.store.get_session(self.task.id)
        turn = session.turn_count if session else 0
        raw_evidence = arguments.get("evidence_artifact_ids")
        cited = [str(item) for item in raw_evidence] if isinstance(raw_evidence, list) else []
        raw_claims = arguments.get("claims")
        if isinstance(raw_claims, list):
            cited.extend(
                str(artifact_id)
                for claim in raw_claims
                if isinstance(claim, dict) and isinstance(claim.get("evidence_artifact_ids"), list)
                for artifact_id in claim["evidence_artifact_ids"]
            )
        cited = list(dict.fromkeys(cited))
        self.store.append_agent_event(
            self.task.id,
            "FINISH_ATTEMPTED",
            self._lifecycle_event_payload(
                turn=turn, code="VALIDATION_PENDING", missing=[],
                evidence_artifact_ids=cited, terminal=False,
            ),
            solver_id=self.solver_id,
        )
        try:
            if self.task.mode != "ctf" and "flag" in arguments:
                raise ValueError("flag is not a valid finish_session field outside CTF mode")
            submission = FinishSubmission.model_validate(arguments)
        except Exception as exc:
            result = {
                "accepted": False,
                "code": "INVALID_FINISH_SUBMISSION",
                "message": self._safe_model_content(str(exc))[:1200],
                "missing": ["valid finish_session arguments"],
                "evidence_artifact_ids": cited,
                "retryable": True,
                "details": {},
            }
            self.last_finish_rejection = result
            self.store.append_agent_event(
                self.task.id,
                "FINISH_REJECTED",
                self._lifecycle_event_payload(
                    turn=turn, code=result["code"], missing=result["missing"],
                    evidence_artifact_ids=cited, terminal=False,
                ),
                solver_id=self.solver_id,
            )
            return {"ok": False, "terminal": False, "validation": result, **result}

        result_model = validator_for(self.task.mode).validate(
            context=CompletionValidationContext(
                task=self.task, solver_id=self.solver_id, store=self.store,
                artifact_text=self.artifacts.text,
                remote_flag_verifier=self.remote_flag_verifier,
            ),
            submission=submission,
        )
        result = result_model.model_dump(mode="json")
        event_payload = self._lifecycle_event_payload(
            turn=turn, code=result_model.code, missing=result_model.missing,
            evidence_artifact_ids=result_model.evidence_artifact_ids,
            terminal=result_model.accepted,
        )
        if not result_model.accepted:
            self.last_finish_rejection = result
            self.store.append_agent_event(self.task.id, "FINISH_REJECTED", event_payload, solver_id=self.solver_id)
            return {"ok": False, "terminal": False, "validation": result, **result}

        self.last_finish_rejection = None
        proof_id = result_model.evidence_artifact_ids[0] if result_model.evidence_artifact_ids else ""
        self.terminal_outcome = SessionOutcome(
            status="completed",
            stop_reason="finish_accepted",
            turn_count=turn,
            summary=self._safe_model_content(submission.summary),
            evidence_artifact_ids=result_model.evidence_artifact_ids,
            details={
                "coverage": [self._safe_model_content(item) for item in submission.coverage],
                "limitations": [self._safe_model_content(item) for item in submission.limitations],
                "claims": [item.model_dump(mode="json") for item in submission.claims],
                "structured_result": {"flag": submission.flag, "proof_artifact_id": proof_id, "verification": result_model.details.get("verification") or "completion_validator"} if submission.flag else {},
                "validator_code": result_model.code,
                "terminal": True,
            },
        )
        return {"ok": True, "terminal": True, "status": "completed", "validation": result, **result}


# Import compatibility only. New handler composition uses TaskCompletionHandler.
CompletionService = LegacyCompletionService


class CapabilityToolHandler(HandlerRuntime):
    def __init__(
        self,
        state: HandlerState,
        *,
        recorder: ActionRecorder,
        artifacts: ArtifactService,
        observer: ObserverExecutionCoordinator,
        strategy: StrategyResolver,
    ) -> None:
        super().__init__(state)
        self.recorder = recorder
        self.artifacts = artifacts
        self.observer_service = observer
        self.strategy = strategy

    def handle(self, **kwargs: Any) -> dict[str, Any]:
        return self._handle_capability_dispatch(**kwargs)

    def _handle_capability_dispatch(self, *, call: dict[str, Any], tool_name: str, arguments: dict[str, Any], governance: dict[str, Any]) -> dict[str, Any]:
        name = tool_name
        capability = self.tool_by_name.get(name)
        if capability is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        registered = self.registry.get(capability)
        if registered is None:
            return {"ok": False, "error": f"capability unavailable: {capability}"}
        try:
            self.registry.validate(capability, arguments)
        except Exception as exc:
            return {"ok": False, "error": f"invalid {capability} arguments: {str(exc)[:800]}"}

        action_id = f"act_{uuid4().hex[:12]}"
        risk = registered.spec.risk
        if capability == "http.request" and str(arguments.get("method") or "GET").upper() != "GET":
            risk = "active"
        card, step = self.strategy.resolve(governance)
        rationale = str(governance.get("rationale") or "").strip()[:500]
        expected_outcome = str(governance.get("expected_outcome") or "").strip()[:500]
        retry_reason = str(governance.get("retry_reason") or "").strip()[:500]
        alternative_analysis = str(governance.get("alternative_analysis") or "").strip()[:500]
        try:
            effect = _effect_from_governance(governance)
        except ValueError as exc:
            return {"ok": False, "status": "blocked", "error": {"code": "INVALID_EFFECT", "message": str(exc), "retryable": False}}
        if step is not None:
            expected_outcome = expected_outcome or step.success_marker or step.expected_request
            rationale = rationale or f"Validate strategy step: {step.title}"
        if not rationale:
            rationale = self._default_rationale(capability, arguments, expected_outcome)
        try:
            input_ref, action_target, actual_target = self._action_resource(capability, arguments)
        except ValueError as exc:
            return {"ok": False, "status": "blocked", "error": {"code": "TARGET_REF_INVALID", "message": str(exc), "retryable": False}}
        authorization = is_allowed(
            tool=capability,
            target=actual_target,
            task=self.task,
            risk=risk,
            action=str(arguments.get("method") or capability),
            sandboxed=self.task.execution_policy.local_compute.mode == "isolated",
        )
        high_side_effect = capability == "http.request" and str(arguments.get("method") or "GET").upper() in {"PUT", "PATCH", "DELETE"}
        if high_side_effect and (
            effect.scope != "target"
            or effect.persistence != "persistent"
            or effect.description == ActionEffect().description
            or not alternative_analysis
        ):
            self.store.append_agent_event(
                self.task.id,
                "ACTION_VALIDATION_FAILED",
                {"capability": capability, "reason": "high_side_effect_analysis_required"},
                solver_id=self.solver_id,
            )
            return {
                "ok": False,
                "error": {"code": "HIGH_IMPACT_EFFECT_REQUIRED", "message": "persistent-state HTTP actions require _tga.effect and _tga.alternative_analysis"},
            }
        action = ActionSpec(
            id=action_id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            **_host_action_fields(governance),
            kind=registered.spec.kind,
            capability=capability,
            target=action_target,
            arguments=arguments,
            rationale=rationale,
            risk=risk,
            strategy_card_id=card.id if card else None,
            strategy_step_id=step.id if step else None,
            expected_outcome=expected_outcome,
            retry_reason=retry_reason,
            alternative_analysis=alternative_analysis,
            effect=effect,
            input_id=input_ref.id if input_ref else None,
            target_ref=input_ref.id if input_ref else None,
            actual_target=actual_target,
            authorization={
                **authorization.model_dump(mode="json"),
                "tool_call_id": str(call.get("id") or ""),
                "provider_tool_name": name,
            },
            provenance=input_ref.provenance.model_dump(mode="json") if input_ref else {},
        )
        if not authorization.allowed:
            if authorization.code == "APPROVAL_REQUIRED":
                approval_expires_at = self.recorder.pending(action)
                with self.store.transaction():
                    self.store.append_agent_event(
                        self.task.id,
                        "ACTION_AWAITING_APPROVAL",
                        {
                            "action_id": action.id,
                            "capability": action.capability,
                            "target": action.actual_target or action.target,
                            "risk": action.risk,
                            "rationale": action.rationale,
                            "effect": action.effect.model_dump(mode="json"),
                            "alternative_analysis": action.alternative_analysis,
                            "approval_expires_at": approval_expires_at,
                            "authorization": action.authorization,
                        },
                        solver_id=self.solver_id,
                    )
                    if self.task.schema_version == 6 and action.governed_action_id:
                        SolverApprovalCoordinator(self.store).await_approval(
                            solver_id=self.solver_id,
                            intent_id=action.intent_id,
                        )
                    else:
                        SessionCoordinator(self.store).await_approval(
                            task_id=self.task.id,
                            action_id=action.id,
                        )
                return {
                    "ok": False,
                    "status": "pending_approval",
                    "action_id": action.id,
                    "approval_required": True,
                    "_defer_tool_result": True,
                }
            blocked = ActionResult(
                action_id=action.id, task_id=self.task.id, solver_id=self.solver_id,
                status="blocked", summary=authorization.reason,
                error=TGAError(code=authorization.code or "POLICY_DENIED", message=authorization.reason, retryable=authorization.retryable),
            )
            self.recorder.block(action, blocked)
            self.store.append_agent_event(
                self.task.id,
                "MANAGER_DECISION",
                {"action_id": action.id, "decision": "denied", "input_id": action.input_id, "actual_target": action.actual_target, "authorization": action.authorization},
                solver_id=self.solver_id,
            )
            return {"ok": False, "status": "blocked", "error": blocked.error.model_dump(mode="json"), "authorization": action.authorization}
        repeat = self.recorder.semantic_repeat(action)
        if repeat and not retry_reason:
            blocked = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="blocked",
                summary="semantic repeat requires a reason tied to new evidence, changed parameters, or explicit verification",
                error=TGAError(code="SEMANTIC_REPEAT_REQUIRES_REASON", message="retry_reason is required for an unchanged action"),
            )
            self.recorder.block(action, blocked)
            self.store.append_agent_event(
                self.task.id,
                "SEMANTIC_REPEAT_BLOCKED",
                {"action_id": action.id, "previous_action_id": repeat, "strategy_step_id": action.strategy_step_id},
                solver_id=self.solver_id,
            )
            self.observer_directive = "This semantic action repeats an existing result. Add a retry reason and a new evidence or validation purpose."
            return {"ok": False, "status": "blocked", "error": blocked.error.model_dump(mode="json")}
        return self._execute_action(
            action=action,
            call_id=str(call.get("id") or ""),
            provider_tool_name=name,
            start=True,
        )

    def execute_approved(self, action: ActionSpec) -> dict[str, Any]:
        action = action.model_copy(update={
            "authorization": {
                **action.authorization,
                "approved_action_id": action.id,
            },
        })
        return self._execute_action(
            action=action,
            call_id=str(action.authorization.get("tool_call_id") or ""),
            provider_tool_name=str(action.authorization.get("provider_tool_name") or action.capability),
            start=False,
        )

    def _execute_action(
        self,
        *,
        action: ActionSpec,
        call_id: str,
        provider_tool_name: str,
        start: bool,
    ) -> dict[str, Any]:
        capability = action.capability
        arguments = action.arguments
        registered = self.registry.get(capability)
        if registered is None:
            raise ValueError(f"persisted capability is unavailable: {capability}")
        if start:
            self.recorder.start(action)
        else:
            self.recorder.resume_approved(action)
        self.store.append_agent_event(
            self.task.id,
            "MANAGER_DECISION",
            {
                "action_id": action.id,
                "capability": capability,
                "decision": "approved",
                "strategy_card_id": action.strategy_card_id,
                "strategy_step_id": action.strategy_step_id,
                "expected_outcome": action.expected_outcome,
                "risk": action.risk,
                "input_id": action.input_id,
                "actual_target": action.actual_target,
                "authorization": action.authorization,
                "retry_reason": action.retry_reason or None,
                "alternative_analysis": action.alternative_analysis or None,
                "effect": action.effect.model_dump(mode="json"),
            },
            solver_id=self.solver_id,
        )
        self.store.append_agent_event(
            self.task.id,
            "TOOL_EXECUTION_START",
            {"tool_call_id": call_id, "action_id": action.id, "tool_name": provider_tool_name, "arguments": self._safe_arguments(arguments), "strategy_step_id": action.strategy_step_id, "execution_location": self._execution_location(capability)},
            solver_id=self.solver_id,
        )
        try:
            execution_task = self._execution_task(arguments)
            result = self.executor.execute(
                task=execution_task,
                action=action,
                workspace=self.workspace,
            )
        except Exception as exc:
            result = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="failed",
                summary=f"tool raised: {str(exc)[:800]}",
            )
        with self.store.transaction():
            self.recorder.finish(action, result)
            excerpts: list[dict[str, str]] = []
            new_indexes = []
            for artifact_id in result.artifact_ids:
                artifact = self.artifacts.register(
                    artifact_id, capability, action.actual_target or action.target,
                    input_id=action.input_id, provenance=action.provenance,
                )
                if artifact is None:
                    continue
                self.last_artifact_id = artifact.id
                index = self.artifacts.index(artifact)
                if index is not None:
                    new_indexes.append(index)
                    self.artifacts.attach_strategy_source(action=action, artifact=artifact, index=index)
                excerpts.append({"artifact_id": artifact.id, "content": self.artifacts.excerpt(artifact)})
            expected_marker_found = self.artifacts.expected_marker_found(result)
            updated_card = self.strategies.record_action(
                card_id=action.strategy_card_id,
                step_id=action.strategy_step_id,
                action_id=action.id,
                artifact_ids=result.artifact_ids,
                succeeded=result.status == "succeeded",
                summary=result.summary,
                expected_marker_found=expected_marker_found,
            ) if self.task.schema_version < 6 else None
            if updated_card is not None:
                step_status = next(
                    (item.status for item in updated_card.steps if item.id == action.strategy_step_id), "pending"
                )
                self.store.append_agent_event(
                    self.task.id,
                    "STRATEGY_STEP_UPDATED",
                    {
                        "strategy_card_id": updated_card.id,
                        "strategy_step_id": action.strategy_step_id,
                        "status": step_status,
                        "card_status": updated_card.status,
                        "active_step_id": updated_card.active_step_id,
                        "action_id": action.id,
                        "artifact_ids": result.artifact_ids,
                    },
                    solver_id=self.solver_id,
                )
            for candidate in result.candidate_flags:
                self.store.append_agent_event(
                    self.task.id,
                    "FLAG_CANDIDATE",
                    {"value": candidate, "artifact_ids": result.artifact_ids},
                    solver_id=self.solver_id,
                )
            for finding in result.candidate_findings:
                if finding.task_id != self.task.id:
                    continue
                evidence_id = str(finding.evidence_artifact_id or "")
                evidence = self.store.get_artifact(evidence_id) if evidence_id else None
                may_confirm_legacy = self.task.schema_version < 6
                persisted = finding.model_copy(update={
                    "status": "confirmed" if (
                        may_confirm_legacy
                        and evidence is not None
                        and evidence.task_id == self.task.id
                    ) else "candidate",
                    "evidence_artifact_id": evidence_id or None,
                })
                self.store.add_candidate_finding(persisted)
                if persisted.status == "confirmed" and evidence_id:
                    self.store.confirm_finding(persisted.id, evidence_id)
                self.store.append_agent_event(
                    self.task.id,
                    "FINDING_CONFIRMED" if persisted.status == "confirmed" else "FINDING_CANDIDATE",
                    {
                        "finding_id": persisted.id,
                        "title": persisted.title,
                        "target": persisted.target,
                        "severity": persisted.severity,
                        "status": persisted.status,
                        "evidence_artifact_id": evidence_id or None,
                    },
                    solver_id=self.solver_id,
                )
            payload = {
                "ok": result.status == "succeeded",
                "status": result.status,
                "summary": result.summary,
                "facts": result.facts,
                "leads": result.leads,
                "candidate_flags": result.candidate_flags,
                "candidate_findings": [item.model_dump(mode="json") for item in result.candidate_findings],
                "artifacts": excerpts,
                "error": result.error.model_dump(mode="json") if result.error else None,
            }
            if result.error and result.error.code in {"RATE_LIMITED", "CONCURRENCY_WAIT"}:
                self.store.append_agent_event(
                    self.task.id,
                    result.error.code,
                    {
                        "action_id": action.id,
                        "capability": capability,
                        "phase": "http" if capability == "http.request" else "process",
                        "retryable": result.error.retryable,
                        "error": result.error.model_dump(mode="json"),
                    },
                    solver_id=self.solver_id,
                )
            self.store.append_agent_event(
                self.task.id,
                "TOOL_EXECUTION_END",
                {"tool_call_id": call_id, "action_id": action.id, "tool_name": provider_tool_name, "execution_location": self._execution_location(capability), **payload},
                solver_id=self.solver_id,
            )
            if capability == "http.request":
                http_status = self.artifacts.http_session_metadata(result)
                if http_status:
                    self.store.append_agent_event(
                        self.task.id,
                        "HTTP_SESSION_STATUS",
                        http_status,
                        solver_id=self.solver_id,
                    )
            if capability == "artifact.inspect":
                self.artifact_retrievals += 1
                self.store.append_agent_event(
                    self.task.id,
                    "ARTIFACT_RETRIEVED",
                    {"action_id": action.id, "artifact_ids": result.artifact_ids, "query": arguments.get("query"), "section": arguments.get("section")},
                    solver_id=self.solver_id,
                )
        self.observer_service.review(action=action, result=result)
        self.plan_knowledge.record_action_result(result)
        return payload
    def _execution_task(self, arguments: dict[str, Any]) -> TGATask:
        # Capabilities enforce scope, risk, TLS, and rate limits against this task.
        return self.task
    def _action_resource(self, capability: str, arguments: dict[str, Any]):
        requested_id = str(arguments.get("input_id") or "")
        if capability == "http.request":
            requested_url = str(arguments.get("url") or "")
            base = self.task.task_entry_url or requested_url
            if not base:
                raise ValueError("HTTP request requires an absolute URL or task_entry_url")
            actual = requested_url if requested_url.startswith(("http://", "https://")) else urljoin(base.rstrip("/") + "/", str(arguments.get("path") or ""))
            return None, base, actual
        return None, self.task.id, self.task.id
    @staticmethod
    def _default_rationale(capability: str, arguments: dict[str, Any], expected: str) -> str:
        if capability == "http.request":
            method = str(arguments.get("method") or "GET").upper()
            destination = str(arguments.get("path") or arguments.get("url") or "authorized target")
            base = f"{method} {destination} to collect evidence"
        elif capability == "artifact.inspect":
            base = f"Retrieve a bounded segment from {arguments.get('artifact_id') or 'an Artifact'}"
        else:
            base = f"Use {capability} to advance the active strategy step"
        return (base + (f"; expected: {expected}" if expected else ""))[:500]


class InputToolHandler(HandlerRuntime):
    def __init__(
        self,
        state: HandlerState,
        *,
        recorder: ActionRecorder,
        artifacts: ArtifactService,
        strategy: StrategyResolver,
    ) -> None:
        super().__init__(state)
        self.recorder = recorder
        self.artifacts = artifacts
        self.strategy = strategy

    def handle(self, **kwargs: Any) -> dict[str, Any]:
        return self._handle_input_tool(**kwargs)

    def _handle_input_tool(
        self,
        *,
        call: dict[str, Any],
        name: str,
        arguments: dict[str, Any],
        governance: dict[str, Any],
    ) -> dict[str, Any]:
        files = [
            item for item in self.task.session_input.files
            if self.state.allowed_resource_ids is None
            or item.id in self.state.allowed_resource_ids
        ]
        input_id = str(arguments.get("input_id") or "")
        item = next((candidate for candidate in files if candidate.id == input_id), None) if input_id else None
        card, step = self.strategy.resolve(governance)
        action = ActionSpec(
            id=f"act_{uuid4().hex[:12]}",
            task_id=self.task.id,
            solver_id=self.solver_id,
            **_host_action_fields(governance),
            kind="tool",
            capability=name,
            target=item.container_path if item else self.task.id,
            arguments=arguments,
            rationale=(
                str(governance.get("rationale") or "").strip()
                or (f"Read immutable task input {input_id}" if input_id else "Inspect the immutable Session input manifest")
            )[:500],
            risk="passive",
            strategy_card_id=card.id if card else None,
            strategy_step_id=step.id if step else None,
            expected_outcome=(
                str(governance.get("expected_outcome") or "").strip()
                or (step.success_marker if step else "")
            )[:500],
            retry_reason=str(governance.get("retry_reason") or "").strip()[:500],
            input_id=input_id or None,
            target_ref=input_id or None,
            actual_target=item.container_path if item else self.task.id,
            authorization={
                "allowed": name == "input_list" or item is not None,
                "code": "TASK_INPUT_OWNED" if name == "input_list" or item is not None else "INPUT_NOT_FOUND",
                "reason": "input belongs to this Session manifest" if item is not None else "input_id is not present in this Session manifest",
                "retryable": False,
            },
            provenance=item.provenance.model_dump(mode="json") if item else {},
        )
        if name != "input_list" and item is None:
            return self._block_missing_input(action=action, call=call, name=name, input_id=input_id)
        repeat = self.recorder.semantic_repeat(action)
        if repeat and not action.retry_reason:
            blocked = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="blocked",
                summary="semantic repeat requires an explicit retry reason",
                error=TGAError(code="SEMANTIC_REPEAT_REQUIRES_REASON", message="retry_reason is required for an unchanged input action"),
            )
            self.recorder.block(action, blocked)
            self.store.append_agent_event(
                self.task.id,
                "SEMANTIC_REPEAT_BLOCKED",
                {"action_id": action.id, "previous_action_id": repeat, "strategy_step_id": action.strategy_step_id},
                solver_id=self.solver_id,
            )
            return {"ok": False, "status": "blocked", "error": blocked.error.model_dump(mode="json")}
        self.recorder.start(action)
        self.store.append_agent_event(
            self.task.id,
            "MANAGER_DECISION",
            {
                "action_id": action.id,
                "capability": name,
                "decision": "approved",
                "strategy_card_id": action.strategy_card_id,
                "strategy_step_id": action.strategy_step_id,
                "expected_outcome": action.expected_outcome,
                "risk": action.risk,
                "input_id": action.input_id,
                "actual_target": action.actual_target,
                "authorization": action.authorization,
            },
            solver_id=self.solver_id,
        )
        self.store.append_agent_event(
            self.task.id,
            "TOOL_EXECUTION_START",
            {
                "tool_call_id": call.get("id"),
                "action_id": action.id,
                "tool_name": name,
                "arguments": self._safe_arguments(arguments),
                "strategy_step_id": action.strategy_step_id,
                "execution_location": "Input Store",
            },
            solver_id=self.solver_id,
        )
        started = time.perf_counter()
        artifact: ArtifactRecord | None = None
        artifact_created = False
        workspace = SessionWorkspace(self.run_root / self.task.id)
        try:
            if name == "input_list":
                payload = {"ok": True, **self.task.input_manifest()}
            elif name == "input_get":
                workspace.verified_bytes(item)
                payload = {"ok": True, **item.manifest_item()}
            elif name == "input_read":
                payload = {"ok": True, **workspace.read(
                    item,
                    offset=int(arguments.get("offset") or 0),
                    limit=int(arguments.get("limit") or 16_384),
                )}
                artifact, artifact_created = self.artifacts.save_input_evidence(
                    item=item, operation=name, payload=payload,
                )
            elif name == "input_search":
                payload = {"ok": True, **workspace.search(
                    item,
                    query=str(arguments.get("query") or ""),
                    limit=int(arguments.get("limit") or 20),
                )}
            elif name == "input_view":
                if getattr(self.client, "supports_vision", None) is False:
                    payload = {"ok": False, "code": "MODEL_VISION_UNSUPPORTED", "reason": f"image remains available at {item.container_path}; use an image-analysis/OCR capability", "input_id": input_id}
                else:
                    block = workspace.image_block(item)
                    payload = {"ok": True, "input_id": input_id, "container_path": item.container_path, "content_block_type": "image_url", "_model_content": block}
            elif name == "input_materialize":
                raw = workspace.verified_bytes(item)
                payload = {"ok": True, "input_id": input_id, "workspace_path": item.relative_path, "mcp_path": item.container_path, "sha256": item.sha256, "immutable": True}
                artifact, artifact_created = self.artifacts.save_input_evidence(
                    item=item, operation=name, payload=payload, raw=raw,
                )
            else:
                payload = {"ok": False, "code": "INPUT_TOOL_UNKNOWN", "reason": name}
        except (OSError, ValueError) as exc:
            payload = {"ok": False, "code": "INPUT_RETRIEVAL_FAILED", "reason": self._safe_model_content(str(exc))[:800], "input_id": input_id}
        artifact_ids = [artifact.id] if artifact is not None else []
        provenance = artifact.provenance if artifact is not None else action.provenance
        payload.update({
            "artifact_id": artifact.id if artifact else None,
            "artifact_ids": artifact_ids,
            "provenance": provenance,
        })
        result = ActionResult(
            action_id=action.id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            status="succeeded" if payload.get("ok") else "failed",
            summary=(
                f"{name} returned immutable input evidence"
                if payload.get("ok") else str(payload.get("reason") or f"{name} failed")
            )[:800],
            artifact_ids=artifact_ids,
            candidate_flags=(
                [candidate] if (candidate := self._first_flag(json.dumps(payload, ensure_ascii=False, default=str))) else []
            ),
            error=None if payload.get("ok") else TGAError(
                code=str(payload.get("code") or "INPUT_RETRIEVAL_FAILED"),
                message=str(payload.get("reason") or "input retrieval failed")[:800],
            ),
        )
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        try:
            with self.store.transaction():
                if artifact is not None:
                    self.store.add_artifact(artifact)
                    index = self.artifacts.index(artifact)
                    self.last_artifact_id = artifact.id
                    self.store.append_agent_event(
                        self.task.id,
                        "ARTIFACT_SAVED",
                        {
                            "artifact": artifact.model_dump(mode="json"),
                            "artifact_id": artifact.id,
                            "action_id": action.id,
                            "input_id": input_id,
                            "tool_name": name,
                            "execution_location": "Artifact Store",
                            "indexed": index is not None,
                        },
                        solver_id=self.solver_id,
                    )
                self.recorder.finish(action, result)
                self.store.append_agent_event(
                    self.task.id,
                    "INPUT_ACCESSED",
                    {
                        "input_id": input_id or None,
                        "operation": name,
                        "allowed": bool(payload.get("ok")),
                        "code": payload.get("code"),
                        "artifact_id": artifact.id if artifact else None,
                        "artifact_ids": artifact_ids,
                        "provenance": provenance,
                        "action_id": action.id,
                        "turn": (self.store.get_session(self.task.id).turn_count if self.store.get_session(self.task.id) else 0),
                    },
                    solver_id=self.solver_id,
                )
                self.store.append_agent_event(
                    self.task.id,
                    "TOOL_EXECUTION_END",
                    {
                        "tool_call_id": call.get("id"),
                        "action_id": action.id,
                        "tool_name": name,
                        "execution_location": "Input Store",
                        "status": result.status,
                        "summary": result.summary,
                        "artifact_id": artifact.id if artifact else None,
                        "artifact_ids": artifact_ids,
                        "candidate_flags": result.candidate_flags,
                        "duration_ms": duration_ms,
                        "error": result.error.model_dump(mode="json") if result.error else None,
                    },
                    solver_id=self.solver_id,
                )
        except Exception:
            if artifact is not None and artifact_created:
                self.artifacts.remove_file(artifact)
            raise
        return payload

    def _block_missing_input(
        self, *, action: ActionSpec, call: dict[str, Any], name: str, input_id: str,
    ) -> dict[str, Any]:
        error = TGAError(
            code="INPUT_NOT_FOUND",
            message="input_id is not present in this Session manifest",
            retryable=False,
        )
        result = ActionResult(
            action_id=action.id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            status="blocked",
            summary=error.message,
            error=error,
        )
        with self.store.transaction():
            self.recorder.block(action, result)
            self.store.append_agent_event(
                self.task.id,
                "MANAGER_DECISION",
                {
                    "action_id": action.id,
                    "capability": name,
                    "decision": "denied",
                    "input_id": input_id,
                    "authorization": action.authorization,
                    "reason": error.message,
                },
                solver_id=self.solver_id,
            )
            self.store.append_agent_event(
                self.task.id,
                "TOOL_EXECUTION_END",
                {
                    "tool_call_id": call.get("id"),
                    "action_id": action.id,
                    "tool_name": name,
                    "execution_location": "Input Store",
                    "status": "blocked",
                    "summary": error.message,
                    "artifact_ids": [],
                    "error": error.model_dump(mode="json"),
                },
                solver_id=self.solver_id,
            )
        return {"ok": False, "status": "blocked", "input_id": input_id, "error": error.model_dump(mode="json")}


class MCPToolHandler(HandlerRuntime):
    def __init__(
        self,
        state: HandlerState,
        *,
        recorder: ActionRecorder,
        artifacts: ArtifactService,
        observer: ObserverExecutionCoordinator,
        strategy: StrategyResolver,
    ) -> None:
        super().__init__(state)
        self.recorder = recorder
        self.artifacts = artifacts
        self.observer_service = observer
        self.strategy = strategy

    def direct_names(self) -> set[str]:
        return self._direct_mcp_names()

    def handle(self, **kwargs: Any) -> dict[str, Any]:
        return self._handle_mcp_dispatch(**kwargs)

    def execute_approved(self, action: ActionSpec) -> dict[str, Any]:
        expected_catalog = str(action.authorization.get("catalog_version") or "")
        expected_provider = str(action.authorization.get("mcp_provider_name") or "")
        expected_server = str(action.authorization.get("mcp_server") or "")
        expected_method = str(action.authorization.get("mcp_method") or "")
        # Re-read the catalog at the approval boundary. A route that changed
        # after the user reviewed it must never inherit that approval.
        current_snapshot = self.mcp_manager.snapshot_for_task(
            self.task,
            workspace=self.workspace,
        )
        self.state.mcp_snapshot = current_snapshot
        route = current_snapshot.route(expected_provider)
        if (
            route is None
            or current_snapshot.version != expected_catalog
            or route.server_id != expected_server
            or route.method != expected_method
        ):
            self.recorder.resume_approved(action)
            result = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="failed",
                summary="approved MCP route is no longer available in the pinned catalog",
                error=TGAError(
                    code="APPROVED_MCP_ROUTE_STALE",
                    message="The approved MCP route changed before execution.",
                    retryable=False,
                ),
            )
            self.recorder.finish(action, result)
            return {
                "ok": False,
                "status": "failed",
                "action_id": action.id,
                "error": result.error.model_dump(mode="json"),
            }
        server_config = (
            self.mcp_manager.config.servers.get(route.server_id)
            if self.mcp_manager.config is not None
            else None
        )
        if server_config is None:
            validation_error = "MCP server configuration is unavailable"
        else:
            approved_policy = self.task.execution_policy.model_copy(deep=True)
            approved_policy.high_impact.mode = "allowlisted"
            approved_policy.high_impact.allowed_actions = [f"mcp:{route.server_id}.{route.method}"]
            approved_task = self.task.model_copy(update={"execution_policy": approved_policy})
            validation_error = self.mcp_manager.policy.authorize(
                task=approved_task,
                server=server_config,
                route=route,
                arguments=action.arguments,
            )
        if validation_error:
            self.recorder.resume_approved(action)
            result = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="failed",
                summary="approved MCP arguments failed execution-time validation",
                error=TGAError(
                    code="APPROVED_MCP_VALIDATION_FAILED",
                    message=str(validation_error)[:800],
                    retryable=False,
                ),
            )
            self.recorder.finish(action, result)
            return {
                "ok": False,
                "status": "failed",
                "action_id": action.id,
                "error": result.error.model_dump(mode="json"),
            }
        return self._handle_mcp_tool_call(
            call={
                "id": str(action.authorization.get("tool_call_id") or ""),
                "function": {
                    "name": str(action.authorization.get("provider_tool_name") or expected_provider),
                },
            },
            route=route,
            arguments=action.arguments,
            governance={},
            llm_tool_name=str(action.authorization.get("provider_tool_name") or expected_provider),
            approved_action=action,
        )

    def _direct_mcp_names(self) -> set[str]:
        return {item.provider_name for item in self.task.mcp_capabilities.tools}
    def _handle_mcp_dispatch(self, *, call: dict[str, Any], arguments: dict[str, Any], governance: dict[str, Any], direct: bool) -> dict[str, Any]:
        if not direct:
            return self._handle_mcp_gateway_call(call=call, arguments=arguments, governance=governance)
        name = str((call.get("function") or {}).get("name") or "")
        mcp_route = self.mcp_snapshot.route(name)
        if mcp_route is not None:
            return self._handle_mcp_tool_call(
                call=call,
                route=mcp_route,
                arguments=arguments,
                governance=governance,
                llm_tool_name=name,
            )
        return {"ok": False, "status": "blocked", "error": {"code": "UNKNOWN_MCP_TOOL", "message": name}}
    def _handle_mcp_gateway_call(
        self, *, call: dict[str, Any], arguments: dict[str, Any], governance: dict[str, Any]
    ) -> dict[str, Any]:
        gateway = MCPGateway(manager=self.mcp_manager, task=self.task, snapshot=self.mcp_snapshot)
        action = str(arguments.pop("action", ""))
        server = str(arguments.pop("server", "") or "")
        tool = str(arguments.pop("tool", "") or "")
        query = str(arguments.pop("query", "") or "")
        if action == "call":
            call_arguments = arguments.pop("arguments", {})
            if not isinstance(call_arguments, dict):
                return {"ok": False, "error": "arguments must be an object"}
            if arguments:
                return {"ok": False, "error": f"unknown tga_mcp fields: {', '.join(sorted(arguments))}"}
            try:
                route = gateway.resolve(server=server, tool=tool)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return self._handle_mcp_tool_call(
                call=call,
                route=route,
                arguments=call_arguments,
                governance=governance,
                llm_tool_name=TGA_MCP_TOOL,
            )
        if arguments:
            return {"ok": False, "error": f"unknown tga_mcp fields: {', '.join(sorted(arguments))}"}
        try:
            result = gateway.query(action=action, server=server, tool=tool, query=query)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.store.append_agent_event(
            self.task.id,
            "MCP_CATALOG_QUERY",
            {
                "tool_kind": "mcp",
                "llm_tool_name": TGA_MCP_TOOL,
                "action": action,
                "server": server or None,
                "query": query or None,
                "catalog_version": self.mcp_snapshot.version,
                "llm_tool_call_id": call.get("id"),
            },
            solver_id=self.solver_id,
        )
        return {"ok": True, **result}
    def _handle_mcp_tool_call(
        self,
        *,
        call: dict[str, Any],
        route: MCPToolRoute,
        arguments: dict[str, Any],
        governance: dict[str, Any],
        llm_tool_name: str,
        approved_action: ActionSpec | None = None,
    ) -> dict[str, Any]:
        """Execute a discovered MCP method without exposing host launch data to the model."""

        action_id = approved_action.id if approved_action is not None else f"act_{uuid4().hex[:12]}"
        trace_id = f"trace_{uuid4().hex}"
        card, step = self.strategy.resolve(governance)
        expected_outcome = str(governance.get("expected_outcome") or "").strip()[:500]
        if step is not None:
            expected_outcome = expected_outcome or step.success_marker or step.expected_request
        rationale = str(governance.get("rationale") or "").strip()[:500]
        rationale = rationale or f"Use {route.server_id}.{route.method} to advance the active strategy step"
        server_config = (
            self.mcp_manager.config.servers.get(route.server_id)
            if self.mcp_manager.config is not None
            else None
        )
        mcp_ref = None
        mcp_target = f"{route.server_id}.{route.method}"
        risk = self.mcp_manager.policy.risk_for(server=server_config, method=route.method) if server_config is not None else "active"
        alternative_analysis = str(governance.get("alternative_analysis") or "").strip()[:500]
        try:
            effect = approved_action.effect if approved_action is not None else _effect_from_governance(governance)
        except ValueError as exc:
            return {"ok": False, "status": "blocked", "error": {"code": "INVALID_EFFECT", "message": str(exc), "retryable": False}}
        if approved_action is None and risk == "destructive" and (
            effect.scope == "none"
            or effect.persistence == "none"
            or effect.description == ActionEffect().description
            or not alternative_analysis
        ):
            self.store.append_agent_event(
                self.task.id,
                "ACTION_VALIDATION_FAILED",
                {"capability": route.provider_name, "reason": "high_side_effect_analysis_required"},
                solver_id=self.solver_id,
            )
            return {
                "ok": False,
                "status": "blocked",
                "error": {
                    "code": "HIGH_IMPACT_EFFECT_REQUIRED",
                    "message": "destructive MCP actions require _tga.effect and _tga.alternative_analysis",
                    "retryable": False,
                },
            }
        execution_task = self.task
        if approved_action is not None:
            # The persisted action was already reviewed by the user. Carry a
            # one-call allowlist through the manager's final policy check so
            # approval is not accidentally denied a second time.
            approved_policy = self.task.execution_policy.model_copy(deep=True)
            approved_policy.high_impact.mode = "allowlisted"
            approved_policy.high_impact.allowed_actions = [f"mcp:{route.server_id}.{route.method}"]
            execution_task = self.task.model_copy(update={"execution_policy": approved_policy})
        if server_config is not None and approved_action is None:
            try:
                validation_error = self.mcp_manager.policy.authorize(
                    task=self.task, server=server_config, route=route, arguments=arguments
                )
            except Exception as exc:
                validation_error = f"schema validation failed safely: {exc}"
            if validation_error:
                approval_required = validation_error.startswith("APPROVAL_REQUIRED:")
                code = "APPROVAL_REQUIRED" if approval_required else "INVALID_ARGUMENTS" if validation_error.startswith("arguments") or "schema validation" in validation_error else "POLICY_DENIED"
                error_payload = {
                    "code": code,
                    "message": validation_error,
                    "phase": "policy",
                    "retryable": False,
                    "server": route.server_id,
                    "method": route.method,
                    "trace_id": trace_id,
                }
                denied_action = ActionSpec(
                    id=action_id, task_id=self.task.id, solver_id=self.solver_id,
                    **_host_action_fields(governance),
                    kind="tool", capability=route.provider_name,
                    target=mcp_target, actual_target=mcp_target, arguments=arguments,
                    rationale=rationale, risk=risk, input_id=mcp_ref.id if mcp_ref else None,
                    target_ref=mcp_ref.id if mcp_ref else None,
                    expected_outcome=expected_outcome,
                    alternative_analysis=alternative_analysis,
                    effect=effect,
                    authorization={
                        "allowed": False,
                        "code": code,
                        "reason": validation_error,
                        "required_authorization": "user approval" if approval_required else "global MCP enablement and execution boundaries",
                        "retryable": False,
                        "tool_call_id": str(call.get("id") or ""),
                        "provider_tool_name": llm_tool_name,
                        "mcp_provider_name": route.provider_name,
                        "mcp_server": route.server_id,
                        "mcp_method": route.method,
                        "catalog_version": self.mcp_snapshot.version,
                    },
                    provenance=mcp_ref.provenance.model_dump(mode="json") if mcp_ref else {"source": "mcp", "server_id": route.server_id},
                )
                if approval_required:
                    approval_expires_at = self.recorder.pending(denied_action)
                    with self.store.transaction():
                        self.store.append_agent_event(
                            self.task.id,
                            "ACTION_AWAITING_APPROVAL",
                            {
                                "action_id": denied_action.id,
                                "capability": denied_action.capability,
                                "target": denied_action.actual_target,
                                "risk": denied_action.risk,
                                "rationale": denied_action.rationale,
                                "tool_kind": "mcp",
                                "mcp_server": route.server_id,
                                "mcp_method": route.method,
                                "effect": denied_action.effect.model_dump(mode="json"),
                                "alternative_analysis": denied_action.alternative_analysis,
                                "approval_expires_at": approval_expires_at,
                                "authorization": denied_action.authorization,
                            },
                            solver_id=self.solver_id,
                        )
                        if self.task.schema_version == 6 and denied_action.governed_action_id:
                            SolverApprovalCoordinator(self.store).await_approval(
                                solver_id=self.solver_id,
                                intent_id=denied_action.intent_id,
                            )
                        else:
                            SessionCoordinator(self.store).await_approval(
                                task_id=self.task.id,
                                action_id=denied_action.id,
                            )
                    return {
                        "ok": False,
                        "status": "pending_approval",
                        "action_id": denied_action.id,
                        "approval_required": True,
                        "_defer_tool_result": True,
                    }
                denied_result = ActionResult(
                    action_id=action_id, task_id=self.task.id, solver_id=self.solver_id,
                    status="blocked", summary=validation_error,
                    error=TGAError(code=code, message=validation_error, retryable=False),
                )
                self.recorder.block(denied_action, denied_result)
                self.store.append_agent_event(
                    self.task.id,
                    "ACTION_VALIDATION_FAILED",
                    {"tool_kind": "mcp", "tool_name": route.provider_name, "mcp_server": route.server_id, "mcp_method": route.method, "trace_id": trace_id, "reason": validation_error, "error": error_payload},
                    solver_id=self.solver_id,
                )
                return {"ok": False, "status": "blocked", "server": route.server_id, "method": route.method, "trace_id": trace_id, "error": error_payload}
        action = approved_action or ActionSpec(
            id=action_id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            **_host_action_fields(governance),
            kind="tool",
            capability=route.provider_name,
            target=mcp_target,
            arguments=arguments,
            rationale=rationale,
            risk=risk,
            strategy_card_id=card.id if card else None,
            strategy_step_id=step.id if step else None,
            expected_outcome=expected_outcome,
            retry_reason=str(governance.get("retry_reason") or "")[:500],
            alternative_analysis=alternative_analysis,
            effect=effect,
            input_id=mcp_ref.id if mcp_ref else None,
            target_ref=mcp_ref.id if mcp_ref else None,
            actual_target=mcp_target,
            authorization={
                "allowed": True,
                "code": None,
                "reason": "available from the global MCP registry and permitted by execution boundaries",
                "required_authorization": None,
                "retryable": False,
                "tool_call_id": str(call.get("id") or ""),
                "provider_tool_name": llm_tool_name,
                "mcp_provider_name": route.provider_name,
                "mcp_server": route.server_id,
                "mcp_method": route.method,
                "catalog_version": self.mcp_snapshot.version,
            },
            provenance=mcp_ref.provenance.model_dump(mode="json") if mcp_ref else {"source": "mcp", "server_id": route.server_id},
        )
        repeat = self.recorder.semantic_repeat(action) if approved_action is None else None
        if repeat and not action.retry_reason:
            blocked = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="blocked",
                summary="semantic repeat requires a reason tied to new evidence or changed parameters",
                error=TGAError(code="SEMANTIC_REPEAT_REQUIRES_REASON", message="retry_reason is required for an unchanged MCP action"),
            )
            self.recorder.block(action, blocked)
            self.store.append_agent_event(
                self.task.id,
                "SEMANTIC_REPEAT_BLOCKED",
                {"action_id": action.id, "previous_action_id": repeat, "tool_kind": "mcp", "mcp_server": route.server_id, "mcp_method": route.method},
                solver_id=self.solver_id,
            )
            return {"ok": False, "status": "blocked", "error": blocked.error.model_dump(mode="json")}
        if approved_action is None:
            self.recorder.start(action)
        else:
            self.recorder.resume_approved(action)
        self.store.append_agent_event(
            self.task.id,
            "MANAGER_DECISION",
            {
                "action_id": action.id,
                "decision": "approved",
                "strategy_card_id": action.strategy_card_id,
                "strategy_step_id": action.strategy_step_id,
                "expected_outcome": action.expected_outcome,
                "risk": action.risk,
                "tool_kind": "mcp",
                "mcp_server": route.server_id,
                "mcp_method": route.method,
                "input_id": action.input_id,
                "authorization": action.authorization,
            },
            solver_id=self.solver_id,
        )
        start_payload = {
            "tool_call_id": call.get("id"),
            "llm_tool_call_id": call.get("id"),
            "action_id": action.id,
            "tool_name": llm_tool_name,
            "llm_tool_name": llm_tool_name,
            "routed_tool_name": route.provider_name,
            "tool_kind": "mcp",
            "mcp_server": route.server_id,
            "mcp_method": route.method,
            "trace_id": trace_id,
            "task_id": self.task.id,
            "session_id": self.task.id,
            "solver_id": self.solver_id,
            "turn_number": (self.store.get_session(self.task.id).turn_count if self.store.get_session(self.task.id) else 0),
            "catalog_version": self.mcp_snapshot.version,
            "arguments": redact_sensitive(arguments),
            "strategy_step_id": action.strategy_step_id,
            "execution_location": self._mcp_execution_location(route.server_id),
        }
        self.store.append_agent_event(
            self.task.id,
            "TOOL_EXECUTION_START",
            start_payload,
            solver_id=self.solver_id,
        )
        started = time.perf_counter()
        outcome = self.mcp_manager.call_tool(
            task=execution_task,
            route=route,
            arguments=arguments,
            catalog_version=self.mcp_snapshot.version,
            workspace=self.workspace,
            trace_id=trace_id,
        )
        artifact_ids: list[str] = []
        artifact_error: TGAError | None = None
        artifact: ArtifactRecord | None = None
        artifact_started = time.perf_counter()
        try:
            artifact = self._save_mcp_artifact(
                outcome=outcome,
                route=route,
                arguments=arguments,
                server_config=server_config,
                action_id=action.id,
                llm_tool_call_id=str(call.get("id") or ""),
            )
            artifact = artifact.model_copy(update={"input_id": action.input_id, "provenance": action.provenance})
            artifact_ids.append(artifact.id)
            self.last_artifact_id = artifact.id
        except Exception as exc:
            artifact_error = TGAError(
                code="ARTIFACT_WRITE_FAILED",
                message=self._safe_model_content(str(exc))[:800],
                retryable=True,
            )
        outcome.timings["artifact_write_ms"] = max(0, int((time.perf_counter() - artifact_started) * 1000))
        outcome.timings.setdefault("total_ms", max(0, int((time.perf_counter() - started) * 1000)))
        status = "succeeded" if outcome.ok and artifact_error is None else "failed"
        error = artifact_error or (
            TGAError(
                code=outcome.error.code,
                message=self._safe_model_content(outcome.error.message),
                retryable=outcome.error.retryable,
            )
            if outcome.error
            else None
        )
        content_text = json.dumps(
            {"content": outcome.content, "structured_content": outcome.structured_content},
            ensure_ascii=False,
            default=str,
        )
        candidate = self._first_flag(content_text) if outcome.ok else None
        result = ActionResult(
            action_id=action.id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            status=status,
            summary=(
                f"MCP {route.server_id}.{route.method} returned {len(outcome.content)} content block(s)"
                if outcome.ok
                else f"MCP {route.server_id}.{route.method} failed"
            ),
            artifact_ids=artifact_ids,
            candidate_flags=[candidate] if candidate else [],
            error=error,
        )
        with self.store.transaction():
            if artifact is not None:
                self.store.add_artifact(artifact)
                try:
                    self.artifacts.index(artifact)
                except Exception as exc:
                    self.store.append_agent_event(
                        self.task.id,
                        "ARTIFACT_INDEX_FAILED",
                        {"artifact_id": artifact.id, "trace_id": trace_id, "reason": self._safe_model_content(str(exc))[:800]},
                        solver_id=self.solver_id,
                    )
            self.recorder.finish(action, result)
            if card is not None and self.task.schema_version < 6:
                updated_card = self.strategies.record_action(
                    card_id=action.strategy_card_id,
                    step_id=action.strategy_step_id,
                    action_id=action.id,
                    artifact_ids=artifact_ids,
                    succeeded=status == "succeeded",
                    summary=result.summary,
                )
                if updated_card is not None:
                    updated_step = next((item for item in updated_card.steps if item.id == action.strategy_step_id), None)
                    self.store.append_agent_event(
                        self.task.id,
                        "STRATEGY_STEP_UPDATED",
                        {
                            "strategy_card_id": updated_card.id,
                            "strategy_step_id": action.strategy_step_id,
                            "status": updated_step.status if updated_step else updated_card.status,
                            "card_status": updated_card.status,
                            "active_step_id": updated_card.active_step_id,
                            "action_id": action.id,
                            "artifact_ids": artifact_ids,
                            "trace_id": trace_id,
                        },
                        solver_id=self.solver_id,
                    )
            if candidate and artifact_ids:
                self.store.append_agent_event(
                    self.task.id,
                    "FLAG_CANDIDATE",
                    {"value": candidate, "artifact_ids": artifact_ids, "trace_id": trace_id},
                    solver_id=self.solver_id,
                )
            inline_limit = server_config.max_inline_chars if server_config is not None else 32_000
            spill = len(content_text) > inline_limit
            if spill:
                tool_payload: dict[str, Any] = {
                    "ok": outcome.ok,
                    "server": route.server_id,
                    "method": route.method,
                    "truncated": True,
                    "original_chars": len(content_text),
                    "preview": content_text[: min(2000, inline_limit)],
                    "artifact_id": artifact_ids[0] if artifact_ids else None,
                    "artifact_ids": artifact_ids,
                    "next_action": "Use artifact.inspect with offset/limit or query.",
                }
            else:
                tool_payload = {
                    "ok": outcome.ok,
                    "server": route.server_id,
                    "method": route.method,
                    "is_error": outcome.is_error,
                    "content": self._mcp_inline_content(outcome.content),
                    "structured_content": outcome.structured_content,
                    "artifact_ids": artifact_ids,
                    "truncated": False,
                }
            model_image = self._mcp_image_block(outcome.content)
            if model_image is not None and getattr(self.client, "supports_vision", None) is not False:
                tool_payload["_model_content"] = model_image
                tool_payload["input_id"] = action.input_id
            elif model_image is not None:
                tool_payload["vision_status"] = {
                    "ok": False,
                    "code": "MODEL_VISION_UNSUPPORTED",
                    "reason": "image bytes were preserved in the MCP Artifact but the configured model is marked as text-only",
                }
            tool_payload.update(
                {
                    "status": status,
                    "trace_id": trace_id,
                    "catalog_version": self.mcp_snapshot.version,
                    "artifact_truncated": outcome.artifact_truncated,
                    "error": outcome.error.model_dump(mode="json") if outcome.error else (error.model_dump(mode="json") if error else None),
                }
            )
            end_payload = {
                "tool_call_id": call.get("id"),
                "llm_tool_call_id": call.get("id"),
                "action_id": action.id,
                "tool_name": llm_tool_name,
                "llm_tool_name": llm_tool_name,
                "routed_tool_name": route.provider_name,
                "tool_kind": "mcp",
                "mcp_server": route.server_id,
                "mcp_method": route.method,
                "mcp_request_id": outcome.request_id,
                "request_id": outcome.request_id,
                "trace_id": trace_id,
                "catalog_version": self.mcp_snapshot.version,
                "task_id": self.task.id,
                "session_id": self.task.id,
                "solver_id": self.solver_id,
                "status": status,
                "artifact_ids": artifact_ids,
                "artifact_id": artifact_ids[0] if artifact_ids else None,
                "truncated": spill,
                "artifact_truncated": outcome.artifact_truncated,
                "duration_ms": outcome.timings.get("total_ms", 0),
                "timings": outcome.timings,
                "execution_location": self._mcp_execution_location(route.server_id),
                "error": outcome.error.model_dump(mode="json") if outcome.error else (
                    {**error.model_dump(mode="json"), "phase": "artifact", "server": route.server_id, "method": route.method, "trace_id": trace_id}
                    if error else None
                ),
            }
            self.store.append_agent_event(
                self.task.id,
                "TOOL_EXECUTION_END",
                end_payload,
                solver_id=self.solver_id,
            )
        self.observer_service.review(action=action, result=result)
        return tool_payload
    @staticmethod
    def _mcp_image_block(content: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in content:
            if not isinstance(item, dict):
                continue
            payload = item.get("resource") if isinstance(item.get("resource"), dict) else item
            mime = str(payload.get("mimeType") or payload.get("mime_type") or "")
            encoded = payload.get("data") or payload.get("blob")
            if (item.get("type") == "image" or mime.startswith("image/")) and isinstance(encoded, str):
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError:
                    continue
                if len(raw) > 20 * 1024 * 1024:
                    continue
                return {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/png'};base64,{encoded}"}}
        return None
    @staticmethod
    def _mcp_inline_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep binary/image payloads in Artifacts and out of JSON tool text."""

        projected: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            copy = dict(item)
            if isinstance(copy.get("data"), str):
                copy["data"] = {"stored_in_artifact": True, "encoded_chars": len(copy["data"])}
            if isinstance(copy.get("blob"), str):
                copy["blob"] = {"stored_in_artifact": True, "encoded_chars": len(copy["blob"])}
            if isinstance(copy.get("resource"), dict):
                resource = dict(copy["resource"])
                for field in ("data", "blob"):
                    if isinstance(resource.get(field), str):
                        resource[field] = {"stored_in_artifact": True, "encoded_chars": len(resource[field])}
                copy["resource"] = resource
            projected.append(copy)
        return projected
    def _save_mcp_artifact(
        self,
        *,
        outcome: MCPCallOutcome,
        route: MCPToolRoute,
        arguments: dict[str, Any],
        server_config: Any,
        action_id: str,
        llm_tool_call_id: str,
    ) -> ArtifactRecord:
        keep_sensitive = bool(server_config and server_config.store_sensitive_artifact_values)
        raw_result: Any = outcome.raw_result if outcome.raw_result is not None else outcome.raw_result_json
        content = outcome.content
        structured = outcome.structured_content
        stdout = outcome.stdout
        stderr = outcome.stderr
        if not keep_sensitive:
            raw_result = redact_sensitive(raw_result) if not isinstance(raw_result, str) else self._redact_mcp_text(raw_result)
            content = redact_sensitive(content)
            structured = redact_sensitive(structured)
            stdout = self._redact_mcp_text(stdout)
            stderr = self._redact_mcp_text(stderr)
        payload = {
            "schema_version": 1,
            "trace_id": outcome.trace_id,
            "catalog_version": outcome.catalog_version,
            "mcp_request_id": outcome.request_id,
            "task_id": self.task.id,
            "session_id": self.task.id,
            "solver_id": self.solver_id,
            "action_id": action_id,
            "llm_tool_call_id": llm_tool_call_id,
            "server": route.server_id,
            "method": route.method,
            "arguments": redact_sensitive(arguments),
            "content": content,
            "structured_content": structured,
            "raw_result": raw_result,
            "stdout": stdout,
            "stderr": stderr,
            "isError": outcome.is_error,
            "returncode": outcome.returncode,
            "timed_out": outcome.timed_out,
            "output_truncated": outcome.output_truncated,
            "artifact_truncated": outcome.artifact_truncated,
            "original_bytes": outcome.original_bytes,
            "saved_bytes": outcome.saved_bytes,
            "server_info": outcome.server_info,
            "protocol_version": outcome.protocol_version,
            "timings": outcome.timings,
            "error": outcome.error.model_dump(mode="json") if outcome.error else None,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        limit = int(server_config.max_artifact_bytes) if server_config is not None else 8 * 1024 * 1024
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) > limit:
            original_bytes = len(encoded)
            preview = outcome.raw_result_json or json.dumps(content, ensure_ascii=False, default=str)
            bounded_payload = {
                "schema_version": 1,
                "trace_id": outcome.trace_id,
                "catalog_version": outcome.catalog_version,
                "mcp_request_id": outcome.request_id,
                "task_id": self.task.id,
                "session_id": self.task.id,
                "solver_id": self.solver_id,
                "action_id": action_id,
                "llm_tool_call_id": llm_tool_call_id,
                "server": route.server_id,
                "method": route.method,
                "arguments": {
                    "keys": list(arguments)[:64],
                    "sha256": hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="replace")).hexdigest(),
                    "sensitive_values_saved": False,
                },
                "artifact_truncated": True,
                "original_bytes": original_bytes,
                "saved_bytes": 0,
                "raw_result_preview": preview[: max(64, limit // 2)],
                "next_action": "Increase maxArtifactBytes and rerun if complete raw output is required.",
            }
            while True:
                text = json.dumps(bounded_payload, ensure_ascii=False, indent=2, default=str)
                saved_bytes = len(text.encode("utf-8", errors="replace"))
                if saved_bytes <= limit or len(str(bounded_payload["raw_result_preview"])) <= 64:
                    break
                bounded_payload["raw_result_preview"] = str(bounded_payload["raw_result_preview"])[: max(64, len(str(bounded_payload["raw_result_preview"])) // 2)]
            for _ in range(20):
                text = json.dumps(bounded_payload, ensure_ascii=False, indent=2, default=str)
                current_bytes = len(text.encode("utf-8", errors="replace"))
                if current_bytes > limit and len(str(bounded_payload["raw_result_preview"])) > 16:
                    bounded_payload["raw_result_preview"] = str(bounded_payload["raw_result_preview"])[: len(str(bounded_payload["raw_result_preview"])) // 2]
                    continue
                if bounded_payload["saved_bytes"] == current_bytes:
                    break
                bounded_payload["saved_bytes"] = current_bytes
            outcome.artifact_truncated = True
            outcome.original_bytes = max(outcome.original_bytes, original_bytes)
            outcome.saved_bytes = len(text.encode("utf-8", errors="replace"))
            if outcome.error is None:
                outcome.error = MCPExecutionError(
                    code="OUTPUT_TRUNCATED",
                    message=f"Artifact exceeded maxArtifactBytes; saved {outcome.saved_bytes} of {original_bytes} bytes",
                    phase="artifact_write",
                    retryable=False,
                    server=route.server_id,
                    method=route.method,
                    trace_id=outcome.trace_id,
                )
        artifact_root = task_artifact_root(self.run_root / self.task.id, self.task)
        return ArtifactStore(artifact_root).save_text(
            task_id=self.task.id,
            intent_id=None,
            kind="tool_output",
            text=text,
            tool=route.provider_name,
            target=f"{route.server_id}.{route.method}",
            suffix=".mcp.json",
        )
    @staticmethod
    def _redact_mcp_text(value: str) -> str:
        value = re.sub(
            r'(?i)(["\']?(?:authorization|cookie|token|secret|password|api[_-]?key)["\']?\s*[:=]\s*)["\']?[^"\'\s,}]+',
            r'\1"[REDACTED]"',
            value,
        )
        return value
    def _mcp_execution_location(self, server_id: str) -> str:
        if self.mcp_manager.config is None:
            return "Remote MCP Service"
        server = self.mcp_manager.config.servers.get(server_id)
        if server and server.transport == "stdio":
            return "Docker MCP Container" if server.stdio and server.stdio.source == "docker_image" else "Host MCP Process"
        return "Remote MCP Service"


@dataclass(frozen=True)
class ToolHandlers:
    state: HandlerState
    capability: CapabilityToolHandler
    inputs: InputToolHandler
    mcp: MCPToolHandler
    completion: TaskCompletionHandler
    recorder: ActionRecorder
    artifacts: ArtifactService
    observer: ObserverExecutionCoordinator

    def update_mcp_snapshot(self, snapshot: MCPCatalogSnapshot) -> None:
        self.state.mcp_snapshot = snapshot

    def close(self) -> None:
        self.state.close()


def build_tool_handlers(**kwargs: Any) -> ToolHandlers:
    state = HandlerState(**kwargs)
    recorder = ActionRecorder(state)
    artifacts = ArtifactService(state)
    observer = ObserverExecutionCoordinator(state)
    strategy = StrategyResolver(state)
    capability = CapabilityToolHandler(
        state, recorder=recorder, artifacts=artifacts, observer=observer, strategy=strategy,
    )
    mcp = MCPToolHandler(
        state, recorder=recorder, artifacts=artifacts, observer=observer, strategy=strategy,
    )
    return ToolHandlers(
        state=state,
        capability=capability,
        inputs=InputToolHandler(state, recorder=recorder, artifacts=artifacts, strategy=strategy),
        mcp=mcp,
        completion=TaskCompletionHandler(state, artifacts),
        recorder=recorder,
        artifacts=artifacts,
        observer=observer,
    )

