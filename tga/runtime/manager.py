"""Application manager for the native ReAct runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from tga.contracts import ActionResult, ActionSpec, MemoryEntry, TGATask
from tga.evidence.store import EvidenceStore, utc_now
from tga.inputs import task_artifact_root
from tga.models.bootstrap import build_model_client
from tga.models.bootstrap import model_config_status
from tga.runtime.agent_session import AgentSessionRunner
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.errors import RuntimeConfigurationError
from tga.runtime.service import require_current_task_schema
from tga.runtime.strategy import StrategyService
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
            executor = self.executor or self._default_executor(task)
            runner = AgentSessionRunner(
                task=task,
                store=store,
                run_root=self.run_root,
                client=client,
                executor=executor,
                solver_id=solver_id,
                max_turns=self.limits.max_turns,
                mcp_manager=self.mcp_manager,
                remote_flag_verifier=self.remote_flag_verifier,
            )
            coordinator.start(task_id=task.id, solver_id=runner.solver_id)
            outcome = runner.run()
            current = store.get_session(task.id)
            if current is not None and current.status == "running" and outcome.status != "running":
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
            session, _ = SessionCoordinator(store).ensure_runtime(
                task=task,
                max_turns=self.limits.max_turns,
            )
            if session.status in {"completed", "cancelled", "failed"}:
                return {"accepted": False, "status": session.status, "reason": "terminal_session"}
            if session.status not in {"created", "running"}:
                return {"accepted": False, "status": session.status, "reason": "session_not_startable"}
            if initial_hint and initial_hint.strip():
                self._record_user_hint(store=store, task=task, content=initial_hint)
            if not store.list_strategy_cards(task_id):
                initial_input = task.session_input.prompt.strip() or task.goal
                card = StrategyService(store).ensure_from_hint(task=task, hint_id=None, content=initial_input)
                store.append_agent_event(
                    task_id,
                    "STRATEGY_CARD_CREATED",
                    {"strategy_card_id": card.id, "source": "session_input.prompt", "status": card.status},
                )
            return {"accepted": True, "status": session.status}
        finally:
            if should_close:
                store.close()

    def control_session(self, *, task_id: str, action: str, action_id: str | None = None) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            session = store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            expire_pending_approvals(store, task_id)
            session = store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            coordinator = SessionCoordinator(store)
            if action == "pause":
                session = coordinator.pause(task_id=task_id, reason="user_paused")
            elif action == "resume":
                if session.status not in {"paused", "blocked"}:
                    return {"status": session.status, "accepted": False, "reason": "session_not_paused"}
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
                    session = coordinator.cancel(task_id=task_id, reason="user_cancelled")
            elif action in {"approve_action", "reject_action"} and action_id:
                if session.status != "awaiting_approval":
                    return {"accepted": False, "status": session.status, "reason": "session_not_awaiting_approval"}
                pending = store.get_action(task_id, action_id)
                if pending is None:
                    return {"accepted": False, "status": session.status, "reason": "action_not_found"}
                target_status = "approved" if action == "approve_action" else "rejected"
                try:
                    with store.transaction():
                        store.update_action_status(action_id, target_status, expected_status="pending_approval")
                        store.append_agent_event(
                            task_id,
                            "ACTION_APPROVED" if target_status == "approved" else "ACTION_REJECTED",
                            {"action_id": action_id, "status": target_status},
                            solver_id=str(pending.get("solver_id") or "") or None,
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

    def add_hint(self, *, task_id: str, content: str) -> dict[str, Any]:
        store, should_close = self._store_for(task_id)
        try:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            require_current_task_schema(task)
            entry = self._record_user_hint(store=store, task=task, content=content)
            return {"accepted": True, "memory_id": entry.id}
        finally:
            if should_close:
                store.close()

    @staticmethod
    def _record_user_hint(*, store: EvidenceStore, task: TGATask, content: str) -> MemoryEntry:
        text = content.strip()
        if not text:
            raise ValueError("hint must not be empty")
        if len(text) > 800:
            raise ValueError("hint exceeds 800 characters")
        now = utc_now()
        entry = MemoryEntry(
            id=f"memory_{uuid4().hex[:12]}",
            task_id=task.id,
            kind="hint",
            content=text,
            source="user",
            created_at=now,
            updated_at=now,
        )
        with store.transaction():
            store.add_memory(entry)
            store.append_agent_event(task.id, "USER_HINT", {"memory_id": entry.id, "content": text})
            store.append_agent_event(task.id, "MEMORY_UPSERTED", {"memory_id": entry.id, "kind": "hint", "source": "user"})
            card = StrategyService(store).ensure_from_hint(task=task, hint_id=entry.id, content=text)
            store.append_agent_event(
                task.id,
                "STRATEGY_CARD_CREATED",
                {
                    "strategy_card_id": card.id,
                    "hint_id": entry.id,
                    "status": card.status,
                    "sources": [source.model_dump(mode="json") for source in card.sources],
                },
            )
        return entry

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
