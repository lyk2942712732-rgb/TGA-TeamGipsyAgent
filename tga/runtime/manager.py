"""Application manager for the native ReAct runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.evidence.database import utc_now
from tga.inputs import task_artifact_root
from tga.models.bootstrap import build_model_client
from tga.models.bootstrap import model_config_status
from tga.application.services.intervention_service import InterventionService
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.lifecycle_service import TaskLifecycleService
from tga.runtime.errors import RuntimeConfigurationError
from tga.runtime.service import TaskRuntimeService, require_current_task_schema
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.agents import SolverRunner
from tga.runtime.scheduling import (
    ActiveRunRegistry,
    ModelCallLimiter,
    SolverRunCompletion,
    SolverRunPool,
)
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


class RuntimeExecutionResources(Protocol):
    sandbox_manager: Any

    def fencing_token(self, solver_id: str) -> int: ...


@dataclass(frozen=True)
class DefaultRuntimeExecutionResources:
    sandbox_manager: Any
    fencing_token_provider: Any
    execution_context: Any | None = None

    def fencing_token(self, solver_id: str) -> int:
        return int(self.fencing_token_provider(solver_id))


class Manager:
    def __init__(
        self,
        *,
        store: EvidenceStore | None = None,
        run_root: str | Path | None = None,
        executor: RuntimeExecutionResources | None = None,
        mcp_manager: MCPManager | None = None,
        model_client: Any | None = None,
        remote_flag_verifier: Any | None = None,
    ) -> None:
        self.store = store
        self.run_root = Path(run_root or os.environ.get("TGA_RUN_ROOT", "runs"))
        self.executor = executor
        self.mcp_manager = mcp_manager or MCPManager(
            cache_path=self.run_root / "mcp-cache.json",
        )
        self.model_client = model_client
        self.remote_flag_verifier = remote_flag_verifier
        self.limits = RuntimeLimits.from_environment()
        self.runtime_owner_id = f"manager_{uuid4().hex}"

    def run_session(self, task_id: str) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            expire_pending_approvals(store, task_id)
            require_current_task_schema(task)
            self._require_model_snapshot(task)
            persistence = PersistenceBundle(store)
            orchestrator = TaskOrchestrator(task=task, repositories=persistence)
            state = orchestrator.bootstrap()
            orchestrator.recover()
            state = orchestrator.state()
            supervisor_id = state.supervisor_solver_id
            if not supervisor_id:
                raise RuntimeError("TaskOrchestrator did not provision a Supervisor")
            coordinator = SessionCoordinator(store)
            lifecycle = TaskLifecycleService(task=task, store=store)
            session = coordinator.ensure_session(
                task=task, max_turns=self.limits.max_turns,
                supervisor_solver_id=supervisor_id,
            )
            if session.status == "awaiting_approval":
                return TaskRuntimeService(run_root=self.run_root).runtime_snapshot(task.id)
            executor = self.executor or self._default_executor(task, store)
            solver_id = self._next_solver_id(
                persistence, task.id, preferred_id=supervisor_id
            )
            while solver_id is not None:
                queued_runs = tuple(
                    run for run in persistence.orchestration.list_solver_runs(task.id)
                    if run.orchestration_role == "worker" and run.state in {
                        "queued", "retry_queued",
                    }
                )
                if queued_runs:
                    self._run_worker_batch(
                        task=task,
                        database_path=store.db_path,
                        client=self.model_client,
                        runs=queued_runs,
                        max_active_workers=state.max_active_workers,
                    )
                    orchestrator.reconcile_solver_runs()
                    current = store.get_session(task.id)
                    if current is None or current.status not in {"created", "running"}:
                        break
                    solver_id = self._next_solver_id(
                        persistence, task.id, preferred_id=supervisor_id
                    )
                    continue
                coordinator.start(task_id=task.id, solver_id=solver_id)
                solver = persistence.solvers.get_solver(solver_id)
                if solver is None:
                    raise RuntimeError(f"scheduled Solver disappeared: {solver_id}")
                client = self.model_client or build_model_client(snapshot=solver.model_snapshot)
                if client is None:
                    raise RuntimeConfigurationError(
                        code="CREDENTIAL_UNAVAILABLE",
                        message=f"runtime model for solver {solver_id} is unavailable",
                    )
                runner = SolverRunner(
                    task=task, store=store, run_root=self.run_root, client=client,
                    executor=executor, solver_id=solver_id,
                    max_turns=self.limits.max_turns, mcp_manager=self.mcp_manager,
                    remote_flag_verifier=self.remote_flag_verifier,
                )
                outcome = runner.run()
                current = store.get_session(task.id)
                if current is None or current.status != "running":
                    break
                if outcome.status in {
                    "blocked", "failed", "cancelled"
                }:
                    lifecycle.apply(solver_id=solver_id, outcome=outcome)
                    break
                if outcome.status == "awaiting_approval":
                    coordinator.apply(task_id=task.id, solver_id=solver_id, outcome=outcome)
                    break
                if outcome.status == "completed" and solver.orchestration_role == "supervisor":
                    coordinator.apply(
                        task_id=task.id, solver_id=solver_id, outcome=outcome
                    )
                    break
                solver_id = self._next_solver_id(
                    persistence, task.id, preferred_id=supervisor_id
                )
            current = store.get_session(task.id)
            if current is not None and current.status == "running" and solver_id is None:
                lifecycle.block(
                    reason="no_runnable_solver",
                    turn_count=current.turn_count, solver_id=supervisor_id,
                )
            current = store.get_session(task.id)
            if current is not None and current.status in {"completed", "cancelled", "failed"}:
                self._release_sandboxes(store, task.id)
            return TaskRuntimeService(run_root=self.run_root).runtime_snapshot(task.id)
        finally:
            if should_close:
                store.close()

    def _run_worker_batch(
        self,
        *,
        task: TGATask,
        database_path: Path,
        client: Any | None,
        runs: tuple,
        max_active_workers: int,
    ) -> tuple[SolverRunCompletion, ...]:
        model_call_limit = max(
            1,
            min(
                max_active_workers,
                int(task.execution_budget.get("max_concurrent_model_calls", max_active_workers)),
            ),
        )
        model_call_limiter = ModelCallLimiter(model_call_limit)
        def repository_factory() -> PersistenceBundle:
            return PersistenceBundle.open(database_path)

        def execute(run, context) -> SolverRunCompletion:
            worker_store = EvidenceStore(database_path)
            retain_sandbox = False
            try:
                context.assert_active()
                executor = self.executor or self._default_executor(
                    task,
                    worker_store,
                    execution_context=context,
                )
                selected_client = client
                if selected_client is None:
                    solver = PersistenceBundle(worker_store).solvers.get_solver(run.solver_id)
                    if solver is None:
                        raise RuntimeError(f"scheduled Solver disappeared: {run.solver_id}")
                    selected_client = build_model_client(snapshot=solver.model_snapshot)
                if selected_client is None:
                    raise RuntimeConfigurationError(
                        code="CREDENTIAL_UNAVAILABLE",
                        message=f"runtime model for solver {run.solver_id} is unavailable",
                    )
                runner = SolverRunner(
                    task=task,
                    store=worker_store,
                    run_root=self.run_root,
                    client=selected_client,
                    executor=executor,
                    solver_id=run.solver_id,
                    max_turns=self.limits.max_turns,
                    mcp_manager=self.mcp_manager,
                    remote_flag_verifier=self.remote_flag_verifier,
                    execution_context=context,
                    model_call_limiter=model_call_limiter,
                )
                context.assert_active()
                outcome = runner.run()
                retain_sandbox = outcome.status == "awaiting_approval"
                context.assert_active()
                state = {
                    "completed": "completed",
                    "cancelled": "cancelled",
                    "awaiting_approval": "waiting_approval",
                }.get(outcome.status, "failed")
                error = outcome.error or {}
                return SolverRunCompletion(
                    state=state,
                    error_code=str(error.get("code") or "") or None,
                    error_message=str(error.get("message") or outcome.stop_reason or "") or None,
                    value=outcome,
                )
            finally:
                if not retain_sandbox:
                    self._release_sandboxes(
                        worker_store, task.id, solver_run_id=run.id
                    )
                worker_store.close()

        return SolverRunPool(
            repository_factory=repository_factory,
            owner_id=self.runtime_owner_id,
            max_active_workers=max_active_workers,
        ).run(task.id, runs, execute)

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
            orchestrator = TaskOrchestrator(
                task=task, repositories=PersistenceBundle(store)
            )
            state = orchestrator.bootstrap()
            if not state.supervisor_solver_id:
                raise RuntimeError("TaskOrchestrator did not provision a Supervisor")
            session = SessionCoordinator(store).ensure_session(
                task=task, max_turns=self.limits.max_turns,
                supervisor_solver_id=state.supervisor_solver_id,
            )
            if session.status in {"completed", "cancelled", "failed"}:
                return {"accepted": False, "status": session.status, "reason": "terminal_session"}
            if session.status not in {"created", "running"}:
                return {"accepted": False, "status": session.status, "reason": "session_not_startable"}
            if initial_hint and initial_hint.strip():
                InterventionService(PersistenceBundle(store)).record(
                    task_id=task.id,
                    kind="hint",
                    content=initial_hint.strip(),
                    actor_id="user",
                )
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
            lifecycle = TaskLifecycleService(task=task, store=store)
            if action == "pause":
                ActiveRunRegistry.cancel_task(task_id, "task_paused")
                session = lifecycle.pause(reason="user_paused")
            elif action == "resume":
                if session.status not in {"paused", "blocked"}:
                    return {"status": session.status, "accepted": False, "reason": "session_not_paused"}
                session = lifecycle.resume()
            elif action == "cancel":
                ActiveRunRegistry.cancel_task(task_id, "task_cancelled")
                with store.transaction():
                    governance = PersistenceBundle(store).tool_governance
                    for pending in governance.list_actions(
                        task_id, status="pending_approval", limit=1_000
                    ):
                        pending_id = str(pending["id"])
                        governance.transition(
                            pending_id, "cancelled", expected_status="pending_approval"
                        )
                        approval = governance.get_approval_for_action(pending_id)
                        if approval is not None and approval["status"] == "pending":
                            governance.decide_approval(
                                pending_id, "cancelled", expected_status="pending"
                            )
                        store.append_agent_event(
                            task_id,
                            "ACTION_CANCELLED",
                            {"action_id": pending_id, "status": "cancelled", "reason": "user_cancelled"},
                            solver_id=str(pending.get("solver_id") or "") or None,
                        )
                    session = lifecycle.cancel(reason="user_cancelled")
                self._release_sandboxes(store, task_id)
            elif action in {"approve_action", "reject_action"} and action_id:
                governance = PersistenceBundle(store).tool_governance
                pending = governance.get_action(action_id)
                if pending is None:
                    return {"accepted": False, "status": session.status, "reason": "action_not_found"}
                if pending["task_id"] != task_id or pending["status"] != "pending_approval":
                    return {"accepted": False, "status": session.status, "reason": "action_not_pending_approval"}
                target_status = "approved" if action == "approve_action" else "rejected"
                try:
                    with store.transaction():
                        governance.decide_approval(
                            action_id, target_status, expected_status="pending"
                        )
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
                        remaining = governance.pending_approval_count(
                            task_id, str(pending.get("solver_id") or "")
                        )
                        if remaining == 0:
                            SolverApprovalCoordinator(store).resolve(
                                solver_id=str(pending.get("solver_id") or ""),
                                intent_id=str(pending.get("intent_id") or "") or None,
                            )
                        session = store.get_session(task_id) or session
                except (KeyError, PersistenceConflict):
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
            response_status: str | None = None
            replacement_solver_id: str | None = None
            if action == "pause":
                if current not in {"created", "queued", "ready", "running", "waiting"}:
                    return {
                        "accepted": False, "status": current,
                        "reason": "solver_not_pausable",
                    }
                replacement = repositories.solvers.update_solver_status(
                    solver_id, "paused"
                )
                cancelled_runs = repositories.orchestration.cancel_solver_runs(
                    task_id, solver_id, reason="SOLVER_PAUSED"
                )
                ActiveRunRegistry.cancel_solver(task_id, solver_id, "operator_paused")
                assignment = repositories.orchestration.get_assignment_for_solver(
                    solver_id
                )
                if assignment is not None and assignment.status in {"proposed", "accepted"}:
                    repositories.orchestration.cancel_assignment(
                        assignment.id, finished_at=utc_now()
                    )
                if solver.assigned_intent_id:
                    plan = repositories.plans.get_global_plan(task_id)
                    intent = next(
                        (item for item in plan.intents if item.id == solver.assigned_intent_id),
                        None,
                    ) if plan else None
                    if intent is not None and intent.status not in {
                        "completed", "failed", "cancelled", "blocked"
                    }:
                        repositories.plans.update_intent_status(
                            intent.id, "blocked", expected_status=intent.status
                        )
                for run in cancelled_runs:
                    repositories.events.append_agent_event(
                        task_id,
                        "SOLVER_RUN_CANCELLED",
                        {"run_id": run.id, "reason": "operator_paused"},
                        solver_id=solver_id,
                        intent_id=run.intent_id,
                    )
                event_type = "SOLVER_PAUSED"
            elif action == "resume":
                if current not in {"paused", "blocked"}:
                    return {
                        "accepted": False, "status": current,
                        "reason": "solver_not_resumable",
                    }
                if solver.orchestration_role == "worker" and solver.assigned_intent_id:
                    state = repositories.orchestration.get_state(task_id)
                    if state is None or not state.supervisor_solver_id:
                        raise RuntimeError("Task Supervisor is unavailable")
                    replacement = TaskOrchestrator(
                        task=task, repositories=repositories
                    ).retry_intent(
                        supervisor_solver_id=state.supervisor_solver_id,
                        intent_id=solver.assigned_intent_id,
                    )
                    replacement_solver_id = replacement.solver_id
                    response_status = "queued"
                else:
                    replacement = repositories.solvers.update_solver_status(
                        solver_id, "ready"
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
                cancelled_runs = repositories.orchestration.cancel_solver_runs(
                    task_id, solver_id, reason="SOLVER_CANCELLED"
                )
                ActiveRunRegistry.cancel_solver(task_id, solver_id, "operator_cancelled")
                assignment = repositories.orchestration.get_assignment_for_solver(
                    solver_id
                )
                if assignment is not None and assignment.status in {"proposed", "accepted"}:
                    repositories.orchestration.cancel_assignment(
                        assignment.id, finished_at=utc_now()
                    )
                if solver.assigned_intent_id:
                    plan = repositories.plans.get_global_plan(task_id)
                    intent = next(
                        (item for item in plan.intents if item.id == solver.assigned_intent_id),
                        None,
                    ) if plan else None
                    if intent is not None and intent.status not in {"completed", "failed", "cancelled"}:
                        repositories.plans.update_intent_status(
                            intent.id, "cancelled", expected_status=intent.status
                        )
                for run in cancelled_runs:
                    repositories.events.append_agent_event(
                        task_id,
                        "SOLVER_RUN_CANCELLED",
                        {"run_id": run.id, "reason": "operator_cancelled"},
                        solver_id=solver_id,
                        intent_id=run.intent_id,
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
                "replacement_solver_id": replacement_solver_id,
                "accepted": True,
                "status": response_status or str(replacement.status),
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

    def _store_for(self, task_id: str) -> tuple[EvidenceStore, bool]:
        if self.store is not None:
            return self.store, False
        return EvidenceStore(self.run_root / task_id / "evidence.db"), True

    def _default_executor(
        self,
        task: TGATask,
        store: EvidenceStore,
        *,
        execution_context=None,
    ) -> RuntimeExecutionResources:
        from tga.sandbox import DockerSandboxProvider, SandboxManager, SandboxdProvider, load_sandbox_config
        from tga.sandbox.repository import SandboxInstanceRepository

        sandbox_config, _ = load_sandbox_config()
        sandbox_manager = SandboxManager(
            config=sandbox_config,
            providers={
                "docker_sandbox": DockerSandboxProvider(sandbox_config),
                "sandboxd": SandboxdProvider(sandbox_config),
            },
            repository=SandboxInstanceRepository(store),
            event_repository=store,
        )

        def fencing_token(solver_id: str) -> int:
            if execution_context is not None:
                if solver_id != execution_context.solver_id:
                    raise PermissionError("Solver execution context ownership mismatch")
                execution_context.assert_active()
                return execution_context.fencing_token
            repositories = PersistenceBundle(store)
            return (
                repositories.orchestration.active_run_fencing_token(solver_id)
                or repositories.solvers.lease_fencing_token(solver_id)
                or 1
            )

        return DefaultRuntimeExecutionResources(
            sandbox_manager=sandbox_manager,
            fencing_token_provider=fencing_token,
            execution_context=execution_context,
        )

    def _release_sandboxes(
        self,
        store: EvidenceStore,
        task_id: str,
        *,
        solver_run_id: str | None = None,
    ) -> None:
        from tga.sandbox import DockerSandboxProvider, SandboxManager, SandboxdProvider, load_sandbox_config
        from tga.sandbox.repository import SandboxInstanceRepository

        config, _ = load_sandbox_config()
        repository = SandboxInstanceRepository(store)
        handles = repository.list_active(
            task_id=task_id, solver_run_id=solver_run_id
        )
        if not handles:
            return
        manager = SandboxManager(
            config=config,
            providers={
                "docker_sandbox": DockerSandboxProvider(config),
                "sandboxd": SandboxdProvider(config),
            },
            repository=repository,
            event_repository=store,
        )
        for handle in handles:
            manager.release(handle)

    @staticmethod
    def _next_solver_id(
        persistence: PersistenceBundle, task_id: str, *, preferred_id: str
    ) -> str | None:
        solvers = persistence.solvers.list_solvers(task_id)
        runnable = {
            "created", "queued", "ready", "waiting", "blocked",
        }
        queued_roles = {"reviewer", "reporter"}
        queued = next((
            item for item in solvers
            if item.orchestration_role in queued_roles and str(item.status) in runnable
        ), None)
        if queued is not None:
            return queued.id
        preferred = next((item for item in solvers if item.id == preferred_id), None)
        if preferred is not None and str(preferred.status) in runnable:
            return preferred.id
        return None

    @staticmethod
    def _require_model_snapshot(task: TGATask) -> None:
        snapshots = task.agent_model_snapshots or {"default": task.model_snapshot}
        for agent_id, snapshot in snapshots.items():
            status = (
                model_config_status(snapshot=snapshot)
                if snapshot.provider_id and snapshot.model_id
                else model_config_status()
            )
            verification = status.get("verification") or {}
            if not status.get("configured"):
                raise RuntimeConfigurationError(
                    code="CREDENTIAL_UNAVAILABLE",
                    message=f"runtime model for {agent_id} is not configured",
                )
            if status.get("verification_status") != "verified":
                raise RuntimeConfigurationError(
                    code="MODEL_CONFIGURATION_STALE",
                    message=f"runtime model verification for {agent_id} is not current",
                )
            if verification.get("capability_fingerprint") != snapshot.capability_fingerprint:
                raise RuntimeConfigurationError(
                    code="MODEL_CONFIGURATION_STALE",
                    message=f"runtime model configuration for {agent_id} differs from the task snapshot",
                )


_manager: Manager | None = None


def get_manager() -> Manager:
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager
