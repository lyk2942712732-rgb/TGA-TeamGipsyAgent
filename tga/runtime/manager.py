"""Application manager for the native ReAct runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tga.contracts import ActionResult, ActionSpec, TGATask
from tga.evidence.store import EvidenceStore
from tga.inputs import task_artifact_root
from tga.models.bootstrap import build_model_client
from tga.models.bootstrap import model_config_status
from tga.application.services.intervention_service import InterventionService
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.errors import RuntimeConfigurationError
from tga.runtime.service import require_current_task_schema
from tga.runtime.orchestration import TaskOrchestrator
from tga.tools.mcp_manager import MCPManager


MAX_SESSION_TURNS = 48


@dataclass(frozen=True)
class RuntimeLimits:
    max_turns: int = MAX_SESSION_TURNS

    @classmethod
    def from_environment(cls) -> "RuntimeLimits":
        def bounded(name: str, default: int, hard_max: int) -> int:
            try:
                return max(1, min(int(os.environ.get(name, str(default))), hard_max))
            except ValueError:
                return default

        return cls(max_turns=bounded("TGA_MAX_SESSION_TURNS", MAX_SESSION_TURNS, 512))


class ActionExecutor(Protocol):
    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult: ...


class Manager:
    def __init__(
        self,
        *,
        store: EvidenceStore | None = None,
        run_root: str | Path | None = None,
        executor: ActionExecutor | None = None,
        mcp_manager: MCPManager | None = None,
        model_client: Any | None = None,
        remote_flag_verifier: Any | None = None,
    ) -> None:
        self.store = store
        self.run_root = Path(run_root or os.environ.get("TGA_RUN_ROOT", "runs"))
        self.executor = executor
        self.mcp_manager = mcp_manager or MCPManager(cache_path=self.run_root / "mcp-cache.json")
        self.model_client = model_client
        self.remote_flag_verifier = remote_flag_verifier
        self.limits = RuntimeLimits.from_environment()

    def run_session(self, task_id: str) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            expire_pending_approvals(store, task_id)
            require_current_task_schema(task)
            self._require_model_snapshot(task)
            client = self.model_client or build_model_client()
            if client is None:
                raise RuntimeConfigurationError(
                    code="CREDENTIAL_UNAVAILABLE",
                    message="runtime model is not configured; credentials or provider configuration are unavailable",
                )
            coordinator = SessionCoordinator(store)
            _, solver_id = coordinator.ensure_runtime(
                task=task,
                max_turns=self.limits.max_turns,
                model_name=getattr(client, "model", ""),
            )
            persistence = PersistenceBundle(store)
            orchestrator = TaskOrchestrator(task=task, repositories=persistence)
            durable_solver = orchestrator.ensure_compatibility_supervisor(
                solver_id=solver_id,
                model_name=getattr(client, "model", ""),
            )
            orchestrator.bootstrap()
            if str(durable_solver.status) == "awaiting_approval":
                return store.task_snapshot(task.id)
            executor = self.executor or self._default_executor(task)
            runner_kwargs = dict(
                store=store,
                run_root=self.run_root,
                client=client,
                executor=executor,
                solver_id=solver_id,
                max_turns=self.limits.max_turns,
                mcp_manager=self.mcp_manager,
                remote_flag_verifier=self.remote_flag_verifier,
            )
            coordinator.start(task_id=task.id, solver_id=solver_id)
            runner, outcome = orchestrator.run_compatibility_solver(**runner_kwargs)
            current = store.get_session(task.id)
            if (
                current is not None
                and current.status == "running"
                and outcome.status not in {"running", "awaiting_approval"}
            ):
                coordinator.apply(task_id=task.id, solver_id=runner.solver_id, outcome=outcome)
            return store.task_snapshot(task.id)
        finally:
            if should_close:
                store.close()

    def refresh_mcp_catalog(self) -> dict[str, Any]:
        self.mcp_manager.refresh()
        return self.mcp_manager.status_snapshot()

    def start_session(self, *, task_id: str, initial_hint: str | None = None) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            session, solver_id = SessionCoordinator(store).ensure_runtime(
                task=task,
                max_turns=self.limits.max_turns,
            )
            orchestrator = TaskOrchestrator(
                task=task, repositories=PersistenceBundle(store)
            )
            orchestrator.ensure_compatibility_supervisor(
                solver_id=solver_id,
            )
            orchestrator.bootstrap()
            if session.status in {"completed", "cancelled", "failed"}:
                return {"accepted": False, "status": session.status, "reason": "terminal_session"}
            if session.status not in {"created", "running"}:
                return {"accepted": False, "status": session.status, "reason": "session_not_startable"}
            if initial_hint and initial_hint.strip():
                self._record_user_hint(store=store, task=task, content=initial_hint)
            return {"accepted": True, "status": session.status}
        finally:
            if should_close:
                store.close()

    def control_session(
        self, *, task_id: str, action: str, action_id: str | None = None,
        decision_reason: str = "",
    ) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            session = store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            expire_pending_approvals(store, task_id)
            session = store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            coordinator = SessionCoordinator(store)
            orchestrator = TaskOrchestrator(
                task=task, repositories=PersistenceBundle(store)
            )
            if action == "pause":
                with store.transaction():
                    orchestrator.pause(reason="user_paused")
                    session = coordinator.pause(task_id=task_id, reason="user_paused")
            elif action == "resume":
                if session.status not in {"paused", "blocked"}:
                    return {"status": session.status, "accepted": False, "reason": "session_not_paused"}
                with store.transaction():
                    orchestrator.resume()
                    session = coordinator.resume(task_id=task_id)
            elif action == "cancel":
                with store.transaction():
                    for pending in store.list_actions(task_id):
                        if pending.get("status") != "pending_approval":
                            continue
                        pending_id = str(pending["id"])
                        store.update_action_status(
                            pending_id, "cancelled", expected_status="pending_approval"
                        )
                        store.add_action_result(ActionResult(
                            action_id=pending_id,
                            task_id=task_id,
                            solver_id=str(pending.get("solver_id") or ""),
                            status="cancelled",
                            summary="The task was cancelled while this action awaited approval.",
                            error={
                                "code": "ACTION_CANCELLED",
                                "message": "The task was cancelled before the approval decision.",
                                "retryable": False,
                            },
                        ))
                        store.append_agent_event(
                            task_id,
                            "ACTION_CANCELLED",
                            {"action_id": pending_id, "status": "cancelled", "reason": "user_cancelled"},
                            solver_id=str(pending.get("solver_id") or "") or None,
                        )
                    orchestrator.cancel(reason="user_cancelled")
                    session = coordinator.cancel(task_id=task_id, reason="user_cancelled")
            elif action in {"approve_action", "reject_action"} and action_id:
                pending = store.get_action(task_id, action_id)
                if pending is None:
                    return {"accepted": False, "status": session.status, "reason": "action_not_found"}
                scoped_v6 = bool(
                    task and task.schema_version == 6
                    and pending.get("governed_action_id")
                )
                if not scoped_v6 and session.status != "awaiting_approval":
                    return {"accepted": False, "status": session.status, "reason": "session_not_awaiting_approval"}
                target_status = "approved" if action == "approve_action" else "rejected"
                try:
                    with store.transaction():
                        store.update_action_status(action_id, target_status, expected_status="pending_approval")
                        store.append_agent_event(
                            task_id,
                            "ACTION_APPROVED" if target_status == "approved" else "ACTION_REJECTED",
                            {
                                "action_id": action_id,
                                "status": target_status,
                                "reason": decision_reason or "operator_decision",
                            },
                            solver_id=str(pending.get("solver_id") or "") or None,
                            intent_id=str(pending.get("intent_id") or "") or None,
                        )
                        if target_status == "rejected":
                            store.add_action_result(ActionResult(
                                action_id=action_id,
                                task_id=task_id,
                                solver_id=str(pending.get("solver_id") or ""),
                                status="rejected",
                                summary="The user rejected this high-impact action.",
                                error={
                                    "code": "ACTION_REJECTED_BY_USER",
                                    "message": "The user rejected this high-impact action.",
                                    "retryable": False,
                                },
                            ))
                        if scoped_v6:
                            approval = PersistenceBundle(store).tool_governance.get_approval_for_action(action_id)
                            if approval is not None:
                                PersistenceBundle(store).tool_governance.decide_approval(
                                    action_id, target_status, expected_status="pending"
                                )
                            remaining = store.conn.execute(
                                "SELECT COUNT(*) FROM approvals WHERE task_id=? "
                                "AND solver_id=? AND status='pending'",
                                (task_id, str(pending.get("solver_id") or "")),
                            ).fetchone()[0]
                            if int(remaining) == 0:
                                SolverApprovalCoordinator(store).resolve(
                                    solver_id=str(pending.get("solver_id") or ""),
                                    intent_id=str(pending.get("intent_id") or "") or None,
                                )
                            session = store.get_session(task_id) or session
                        else:
                            session = coordinator.resume(
                                task_id=task_id,
                                reason=f"action_{target_status}:{action_id}",
                            )
                except KeyError:
                    return {"accepted": False, "status": session.status, "reason": "action_not_pending_approval"}
            else:
                return {"accepted": False, "reason": "invalid_control_action"}
            return {"accepted": True, "status": session.status}
        finally:
            if should_close:
                store.close()

    def record_intervention(
        self,
        *,
        task_id: str,
        kind: str,
        content: str,
        scope: str = "task",
        target_id: str | None = None,
    ) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            repositories = PersistenceBundle(store)
            if scope == "solver":
                solver = repositories.solvers.get_solver(str(target_id or ""))
                if solver is None or solver.task_id != task_id:
                    raise PermissionError("intervention Solver does not belong to Task")
            elif scope == "intent":
                plan = repositories.plans.get_global_plan(task_id)
                if plan is None or not any(item.id == target_id for item in plan.intents):
                    raise PermissionError("intervention Intent does not belong to Task")
            result = InterventionService(repositories).record(
                task_id=task_id,
                kind=kind,
                content=content,
                actor_id="user",
                scope=scope,
                target_id=target_id,
            )
            return {
                "schema_version": 6,
                "task_id": task_id,
                "accepted": True,
                "status": "recorded",
                "intervention": result.intervention.model_dump(mode="json"),
                "hint_id": result.hint.id if result.hint else None,
                "directive_id": result.directive.id if result.directive else None,
            }
        finally:
            if should_close:
                store.close()

    def control_solver(
        self,
        *,
        task_id: str,
        solver_id: str,
        action: str,
        reason: str = "operator_request",
    ) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            repositories = PersistenceBundle(store)
            solver = repositories.solvers.get_solver(solver_id)
            if solver is None or solver.task_id != task_id:
                raise PermissionError("Solver does not belong to Task")
            current = str(solver.status)
            if action == "pause":
                if current not in {"created", "queued", "ready", "running", "waiting"}:
                    return {
                        "accepted": False, "status": current,
                        "reason": "solver_not_pausable",
                    }
                replacement = repositories.solvers.update_solver_status(
                    solver_id, "paused"
                )
                event_type = "SOLVER_PAUSED"
            elif action == "resume":
                if current not in {"paused", "blocked"}:
                    return {
                        "accepted": False, "status": current,
                        "reason": "solver_not_resumable",
                    }
                target = "ready" if solver.orchestration_role == "supervisor" else "queued"
                replacement = repositories.solvers.update_solver_status(
                    solver_id, target
                )
                event_type = "SOLVER_STARTED"
            elif action == "cancel":
                if current in {"completed", "failed", "cancelled"}:
                    return {
                        "accepted": False, "status": current,
                        "reason": "solver_terminal",
                    }
                replacement = repositories.solvers.update_solver_status(
                    solver_id, "cancelled"
                )
                event_type = "SOLVER_CANCELLED"
            else:
                return {
                    "accepted": False, "status": current,
                    "reason": "invalid_solver_control_action",
                }
            repositories.events.append_agent_event(
                task_id,
                event_type,
                {"solver_id": solver_id, "action": action, "reason": reason},
                solver_id=solver_id,
                intent_id=solver.assigned_intent_id,
            )
            return {
                "schema_version": 6,
                "task_id": task_id,
                "solver_id": solver_id,
                "accepted": True,
                "status": str(replacement.status),
            }
        finally:
            if should_close:
                store.close()

    def retry_intent(
        self,
        *,
        task_id: str,
        intent_id: str,
        reason: str = "operator_retry",
    ) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            repositories = PersistenceBundle(store)
            state = repositories.orchestration.get_state(task_id)
            plan = repositories.plans.get_global_plan(task_id)
            if state is None or not state.supervisor_solver_id:
                raise RuntimeError("Task Supervisor is unavailable")
            if plan is None or not any(item.id == intent_id for item in plan.intents):
                raise PermissionError("Intent does not belong to Task")
            assignment = TaskOrchestrator(
                task=task, repositories=repositories
            ).retry_intent(
                supervisor_solver_id=state.supervisor_solver_id,
                intent_id=intent_id,
            )
            repositories.events.append_agent_event(
                task_id,
                "INTENT_ASSIGNED",
                {
                    "intent_id": intent_id,
                    "solver_id": assignment.solver_id,
                    "assignment_id": assignment.id,
                    "attempt": assignment.attempt,
                    "reason": reason,
                },
                solver_id=assignment.solver_id,
                intent_id=intent_id,
            )
            return {
                "schema_version": 6,
                "task_id": task_id,
                "accepted": True,
                "status": "assigned",
                "assignment": assignment.model_dump(mode="json"),
            }
        finally:
            if should_close:
                store.close()

    def add_hint(self, *, task_id: str, content: str) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            result = self._record_user_hint(store=store, task=task, content=content)
            assert result.hint is not None
            return {
                "accepted": True,
                "hint_id": result.hint.id,
                "intervention_id": result.intervention.id,
                # Compatibility response field; no MemoryEntry is created.
                "memory_id": result.hint.id,
            }
        finally:
            if should_close:
                store.close()

    @staticmethod
    def _record_user_hint(*, store: EvidenceStore, task: TGATask, content: str):
        text = content.strip()
        if not text:
            raise ValueError("hint must not be empty")
        if len(text) > 800:
            raise ValueError("hint exceeds 800 characters")
        return InterventionService(PersistenceBundle(store)).record(
            task_id=task.id,
            kind="hint",
            content=text,
            actor_id="user",
        )

    def _store_for(self, task_id: str) -> tuple[EvidenceStore, bool]:
        if self.store is not None:
            return self.store, False
        return EvidenceStore(self.run_root / task_id / "evidence.db"), True

    def _default_executor(self, task: TGATask) -> ActionExecutor:
        from tga.capabilities.runtime import ControlledActionExecutor, ExecutionBudget
        from tga.evidence.artifacts import ArtifactStore

        policy = task.execution_policy
        assert policy is not None
        return ControlledActionExecutor(
            artifact_store=ArtifactStore(task_artifact_root(self.run_root / task.id, task)),
            budget=ExecutionBudget(
                http_requests_per_minute=policy.network.rate_limit_per_minute,
                http_burst=max(1, min(policy.network.concurrency, 10)),
                http_concurrency=policy.network.concurrency,
                process_concurrency=policy.local_compute.concurrency,
                http_timeout_s=policy.network.request_timeout_seconds,
                process_timeout_s=policy.local_compute.timeout_seconds,
            ),
        )

    @staticmethod
    def _require_model_snapshot(task: TGATask) -> None:
        if task.model_snapshot is None:
            return
        status = model_config_status()
        verification = status.get("verification") or {}
        if not status.get("configured"):
            raise RuntimeConfigurationError(
                code="CREDENTIAL_UNAVAILABLE",
                message="runtime model is not configured; credentials or provider configuration are unavailable",
            )
        if status.get("verification_status") != "verified":
            raise RuntimeConfigurationError(
                code="MODEL_CONFIGURATION_STALE",
                message="runtime model verification is not current",
            )
        if verification.get("capability_fingerprint") != task.model_snapshot.capability_fingerprint:
            raise RuntimeConfigurationError(
                code="MODEL_CONFIGURATION_STALE",
                message="runtime model configuration differs from the task model snapshot",
            )


_manager: Manager | None = None


def get_manager() -> Manager:
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager
