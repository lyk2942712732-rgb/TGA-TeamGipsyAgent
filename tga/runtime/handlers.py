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
from urllib.parse import urlparse
from uuid import uuid4

from tga.contracts import ActionResult, ActionSpec, ArtifactRecord, TGAError, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence.bundle import PersistenceBundle
from tga.inputs import SessionWorkspace, task_artifact_root
from tga.runtime.coordinator import SessionOutcome
from tga.runtime.handler_services import ArtifactService, ObserverExecutionCoordinator
from tga.runtime.resources import authorized_session_files
from tga.runtime.tool_handlers.plan_knowledge import PlanKnowledgeHandler
from tga.runtime.tool_handlers.task_completion import TaskCompletionHandler
from tga.runtime.observer import DeterministicObserver, ObserverCoordinator
from tga.tools.mcp_manager import MCPCallOutcome, MCPExecutionError, MCPManager
from tga.tools.mcp_policy import redact_sensitive
from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPToolRoute


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
        execution_context: Any | None = None,
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
        self.execution_context = execution_context
        # Authoritative resource scope for this Solver session.
        self.task_spec = PersistenceBundle(store).tasks.get_task_spec(task.id)
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
            "observer",
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


class CapabilityToolHandler(HandlerRuntime):
    def __init__(
        self,
        state: HandlerState,
        *,
        artifacts: ArtifactService,
        observer: ObserverExecutionCoordinator,
    ) -> None:
        super().__init__(state)
        self.artifacts = artifacts
        self.observer_service = observer

    def execute_governed(self, action: ActionSpec) -> dict[str, Any]:
        if action.governed_action_id != action.id:
            raise ValueError("Capability execution requires its governed Action identity")
        capability = action.capability
        arguments = action.arguments
        call_id = str(action.authorization.get("tool_call_id") or "")
        provider_tool_name = str(
            action.authorization.get("provider_tool_name") or capability
        )
        registered = self.registry.get(capability)
        if registered is None:
            raise ValueError(f"governed capability is unavailable: {capability}")
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
            excerpts: list[dict[str, str]] = []
            for artifact_id in result.artifact_ids:
                artifact = self.artifacts.register(
                    artifact_id, capability, action.actual_target or action.target,
                    input_id=action.input_id, provenance=action.provenance,
                )
                if artifact is None:
                    continue
                self.last_artifact_id = artifact.id
                excerpts.append({"artifact_id": artifact.id, "content": self.artifacts.excerpt(artifact)})
            for candidate in result.candidate_flags:
                self.store.append_agent_event(
                    self.task.id,
                    "FLAG_CANDIDATE",
                    {"value": candidate, "artifact_ids": result.artifact_ids},
                    solver_id=self.solver_id,
                )
            payload = {
                "ok": result.status == "succeeded",
                "status": result.status,
                "summary": result.summary,
                "facts": result.facts,
                "leads": result.leads,
                "candidate_flags": result.candidate_flags,
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


class InputToolHandler(HandlerRuntime):
    def __init__(
        self,
        state: HandlerState,
        *,
        artifacts: ArtifactService,
    ) -> None:
        super().__init__(state)
        self.artifacts = artifacts

    def execute_governed(self, action: ActionSpec) -> dict[str, Any]:
        if action.governed_action_id != action.id:
            raise ValueError("Input execution requires its governed Action identity")
        name = action.capability
        arguments = action.arguments
        call_id = str(action.authorization.get("tool_call_id") or "")
        files = authorized_session_files(
            self.task,
            self.state.task_spec,
            self.state.allowed_resource_ids,
        )
        input_id = str(arguments.get("input_id") or "")
        item = next((candidate for candidate in files if candidate.id == input_id), None) if input_id else None
        if name != "input_list" and item is None:
            return self._missing_input(
                action=action, call_id=call_id, name=name, input_id=input_id
            )
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
                "tool_call_id": call_id,
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
                            "indexed": False,
                            "indexing_status": "pending",
                        },
                        solver_id=self.solver_id,
                    )
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
                        "tool_call_id": call_id,
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

    def _missing_input(
        self, *, action: ActionSpec, call_id: str, name: str, input_id: str,
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
            self.store.append_agent_event(
                self.task.id,
                "TOOL_EXECUTION_END",
                {
                    "tool_call_id": call_id,
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
        artifacts: ArtifactService,
        observer: ObserverExecutionCoordinator,
    ) -> None:
        super().__init__(state)
        self.artifacts = artifacts
        self.observer_service = observer

    def direct_names(self) -> set[str]:
        return {item.provider_name for item in self.task.mcp_capabilities.tools}

    def execute_governed(
        self, action: ActionSpec, *, approved: bool = False,
    ) -> dict[str, Any]:
        if action.governed_action_id != action.id:
            raise ValueError("MCP execution requires its governed Action identity")
        expected_provider = str(
            action.authorization.get("mcp_provider_name")
            or action.authorization.get("provider_tool_name")
            or ""
        )
        expected_server = str(action.authorization.get("mcp_server") or "")
        expected_method = str(action.authorization.get("mcp_method") or "")
        expected_catalog = str(action.authorization.get("catalog_version") or "")
        if approved:
            current_snapshot = self.mcp_manager.snapshot_for_task(
                self.task, workspace=self.workspace,
            )
            self.state.mcp_snapshot = current_snapshot
        else:
            current_snapshot = self.mcp_snapshot
        route = current_snapshot.route(expected_provider)
        if (
            route is None
            or route.server_id != expected_server
            or route.method != expected_method
            or current_snapshot.version != expected_catalog
        ):
            return self._validation_failure(
                action,
                code="APPROVED_MCP_ROUTE_STALE" if approved else "MCP_ROUTE_STALE",
                message="The governed MCP route changed before execution.",
            )
        server_config = (
            self.mcp_manager.config.servers.get(route.server_id)
            if self.mcp_manager.config is not None
            else None
        )
        if server_config is None:
            return self._validation_failure(
                action,
                code="MCP_SERVER_UNAVAILABLE",
                message="MCP server configuration is unavailable.",
            )

        execution_task = self.task
        if approved:
            approved_policy = self.task.execution_policy.model_copy(deep=True)
            approved_policy.high_impact.mode = "allowlisted"
            approved_policy.high_impact.allowed_actions = [
                f"mcp:{route.server_id}.{route.method}"
            ]
            execution_task = self.task.model_copy(
                update={"execution_policy": approved_policy}
            )
            validation_error = self.mcp_manager.policy.authorize(
                context=execution_task,
                server=server_config,
                route=route,
                arguments=action.arguments,
            )
            if validation_error:
                return self._validation_failure(
                    action,
                    code="APPROVED_MCP_VALIDATION_FAILED",
                    message=str(validation_error)[:800],
                )

        trace_id = f"trace_{uuid4().hex}"
        call_id = str(action.authorization.get("tool_call_id") or "")
        llm_tool_name = str(
            action.authorization.get("provider_tool_name") or route.provider_name
        )
        self.store.append_agent_event(
            self.task.id,
            "MANAGER_DECISION",
            {
                "action_id": action.id,
                "decision": "approved",
                "risk": action.risk,
                "tool_kind": "mcp",
                "mcp_server": route.server_id,
                "mcp_method": route.method,
                "authorization": action.authorization,
            },
            solver_id=self.solver_id,
        )
        self.store.append_agent_event(
            self.task.id,
            "TOOL_EXECUTION_START",
            {
                "tool_call_id": call_id,
                "llm_tool_call_id": call_id,
                "action_id": action.id,
                "tool_name": llm_tool_name,
                "llm_tool_name": llm_tool_name,
                "routed_tool_name": route.provider_name,
                "tool_kind": "mcp",
                "mcp_server": route.server_id,
                "mcp_method": route.method,
                "trace_id": trace_id,
                "task_id": self.task.id,
                "solver_id": self.solver_id,
                "turn_number": (
                    self.store.get_session(self.task.id).turn_count
                    if self.store.get_session(self.task.id) else 0
                ),
                "catalog_version": current_snapshot.version,
                "arguments": redact_sensitive(action.arguments),
                "execution_location": self._mcp_execution_location(route.server_id),
            },
            solver_id=self.solver_id,
        )
        started = time.perf_counter()
        outcome = self.mcp_manager.call_tool(
            context=execution_task,
            route=route,
            arguments=action.arguments,
            catalog_version=current_snapshot.version,
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
                arguments=action.arguments,
                server_config=server_config,
                action_id=action.id,
                llm_tool_call_id=call_id,
            ).model_copy(update={
                "input_id": action.input_id,
                "provenance": action.provenance,
            })
            artifact_ids.append(artifact.id)
            self.last_artifact_id = artifact.id
        except Exception as exc:
            artifact_error = TGAError(
                code="ARTIFACT_WRITE_FAILED",
                message=self._safe_model_content(str(exc))[:800],
                retryable=True,
            )
        outcome.timings["artifact_write_ms"] = max(
            0, int((time.perf_counter() - artifact_started) * 1000)
        )
        outcome.timings.setdefault(
            "total_ms", max(0, int((time.perf_counter() - started) * 1000))
        )
        status = "succeeded" if outcome.ok and artifact_error is None else "failed"
        error = artifact_error or (
            TGAError(
                code=outcome.error.code,
                message=self._safe_model_content(outcome.error.message),
                retryable=outcome.error.retryable,
            )
            if outcome.error else None
        )
        content_text = json.dumps(
            {
                "content": outcome.content,
                "structured_content": outcome.structured_content,
            },
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
                f"MCP {route.server_id}.{route.method} returned "
                f"{len(outcome.content)} content block(s)"
                if outcome.ok else f"MCP {route.server_id}.{route.method} failed"
            ),
            artifact_ids=artifact_ids,
            candidate_flags=[candidate] if candidate else [],
            error=error,
        )
        with self.store.transaction():
            if artifact is not None:
                self.store.add_artifact(artifact)
            if candidate and artifact_ids:
                self.store.append_agent_event(
                    self.task.id,
                    "FLAG_CANDIDATE",
                    {
                        "value": candidate,
                        "artifact_ids": artifact_ids,
                        "trace_id": trace_id,
                    },
                    solver_id=self.solver_id,
                )
            inline_limit = server_config.max_inline_chars
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
            if model_image is not None and getattr(
                self.client, "supports_vision", None
            ) is not False:
                tool_payload["_model_content"] = model_image
                tool_payload["input_id"] = action.input_id
            elif model_image is not None:
                tool_payload["vision_status"] = {
                    "ok": False,
                    "code": "MODEL_VISION_UNSUPPORTED",
                    "reason": (
                        "image bytes were preserved in the MCP Artifact but the "
                        "configured model is marked as text-only"
                    ),
                }
            tool_payload.update({
                "status": status,
                "trace_id": trace_id,
                "catalog_version": current_snapshot.version,
                "artifact_truncated": outcome.artifact_truncated,
                "error": (
                    outcome.error.model_dump(mode="json")
                    if outcome.error else error.model_dump(mode="json") if error else None
                ),
            })
            self.store.append_agent_event(
                self.task.id,
                "TOOL_EXECUTION_END",
                {
                    "tool_call_id": call_id,
                    "llm_tool_call_id": call_id,
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
                    "catalog_version": current_snapshot.version,
                    "task_id": self.task.id,
                    "solver_id": self.solver_id,
                    "status": status,
                    "artifact_ids": artifact_ids,
                    "artifact_id": artifact_ids[0] if artifact_ids else None,
                    "truncated": spill,
                    "artifact_truncated": outcome.artifact_truncated,
                    "duration_ms": outcome.timings.get("total_ms", 0),
                    "timings": outcome.timings,
                    "execution_location": self._mcp_execution_location(route.server_id),
                    "error": (
                        outcome.error.model_dump(mode="json")
                        if outcome.error else error.model_dump(mode="json") if error else None
                    ),
                },
                solver_id=self.solver_id,
            )
        self.observer_service.review(action=action, result=result)
        return tool_payload

    @staticmethod
    def _validation_failure(
        action: ActionSpec, *, code: str, message: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "failed",
            "action_id": action.id,
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
            },
        }
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
        return ArtifactStore(
            artifact_root, execution_context=self.state.execution_context
        ).save_text(
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
    artifacts: ArtifactService
    observer: ObserverExecutionCoordinator

    def update_mcp_snapshot(self, snapshot: MCPCatalogSnapshot) -> None:
        self.state.mcp_snapshot = snapshot

    def close(self) -> None:
        self.state.close()


def build_tool_handlers(**kwargs: Any) -> ToolHandlers:
    state = HandlerState(**kwargs)
    artifacts = ArtifactService(state)
    observer = ObserverExecutionCoordinator(state)
    capability = CapabilityToolHandler(
        state, artifacts=artifacts, observer=observer,
    )
    mcp = MCPToolHandler(
        state, artifacts=artifacts, observer=observer,
    )
    return ToolHandlers(
        state=state,
        capability=capability,
        inputs=InputToolHandler(state, artifacts=artifacts),
        mcp=mcp,
        completion=TaskCompletionHandler(state, artifacts),
        artifacts=artifacts,
        observer=observer,
    )
