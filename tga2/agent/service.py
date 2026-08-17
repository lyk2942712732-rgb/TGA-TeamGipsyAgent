"""Public task service shared by API, CLI and tests."""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from tga2.agent.graph import RuntimeDeps, TaskGraph
from tga2.agent.nodes import BudgetExceededError, TaskCancelledError
from tga2.agent.roles import (
    AgentSuite,
    LangChainAgentSuite,
    OfflineAgentSuite,
    RoutedAgentSuite,
)
from tga2.agent.schemas import ReportDraft
from tga2.config import Configuration
from tga2.core.models import (
    AgentEvent,
    IntentStatus,
    ResourceRef,
    Task,
    TaskSpec,
    TaskStatus,
    utc_now,
)
from tga2.core.policy import ExecutionPolicy, ToolPolicy
from tga2.core.report import render_markdown
from tga2.core.store import TaskStore
from tga2.core.workspace import TaskWorkspace
from tga2.integrations.model import ModelSettings, build_chat_model
from tga2.projections import (
    event_projection,
    runtime_snapshot_projection,
    task_list_projection,
)
from tga2.skills import SkillRepository


class TaskRuntimeService:
    def __init__(
        self,
        *,
        run_root: str | Path,
        model_settings: ModelSettings | None = None,
        agents: AgentSuite | None = None,
        configuration: Configuration | None = None,
        external_tools: Sequence[BaseTool] = (),
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.configuration = configuration or Configuration(self.run_root / ".config")
        self.skills = SkillRepository(
            self.run_root / ".config" / "skills",
            document_max_bytes=self.configuration.runtime.files.skill_document_max_bytes,
            package_max_bytes=self.configuration.runtime.files.skill_package_max_bytes,
        )
        self._agents_overridden = agents is not None
        self.agents = agents or self._build_agents(model_settings)
        self.external_tools = list(external_tools)

    def configure_model(self, settings: ModelSettings) -> dict[str, Any]:
        """Compatibility hook; model definitions persist in models.json.

        Role assignments in runtime.json remain authoritative, so registering or
        verifying a model never silently changes which model a Solver uses.
        """
        self.reload_configuration()
        return {
            "configured": settings.can_call_model,
            "provider": settings.provider,
            "model": settings.model,
            "offline": not (settings.can_call_model and settings.verified),
        }

    def reload_configuration(self) -> None:
        if not self._agents_overridden:
            self.agents = self._build_agents()

    def set_external_tools(self, tools: Sequence[BaseTool]) -> None:
        self.external_tools = list(tools)

    def _skill_catalog(self, _task: Task) -> str:
        return self.skills.catalog_prompt()

    def _agents_for_task(self, task: Task, store: TaskStore | None = None) -> AgentSuite:
        if self._agents_overridden or store is None:
            return self.agents
        overrides = self._model_overrides(store, task.id)
        return self._build_agents(overrides=overrides) if overrides else self.agents

    def _build_agents(
        self,
        fallback: ModelSettings | None = None,
        overrides: dict[str, tuple[str, str]] | None = None,
    ) -> AgentSuite:
        offline = OfflineAgentSuite()
        roles: dict[str, AgentSuite] = {}
        for role in ("supervisor", "worker", "reviewer", "reporter"):
            role_config = self.configuration.runtime.roles[role]
            role_budget = getattr(self.configuration.runtime.budget.roles, role)
            if role == "supervisor":
                call_limit = role_budget.calls_per_decision
            elif role == "worker":
                call_limit = role_budget.calls_per_attempt
            elif role == "reviewer":
                call_limit = role_budget.calls_per_review
            else:
                call_limit = role_budget.calls_per_report
            parse_retries = 0 if role == "worker" else role_budget.parse_retries
            provider_id, model_id = (overrides or {}).get(
                role, (role_config.model.provider_id, role_config.model.model_id)
            )
            if (provider_id, model_id) == ("offline", "offline"):
                if fallback and fallback.can_call_model and fallback.verified:
                    roles[role] = LangChainAgentSuite(
                        build_chat_model(fallback),
                        self._skill_catalog,
                        self.configuration.agent_prompts(),
                        model_call_limit=call_limit,
                        structured_parse_retries=parse_retries,
                        force_prompt_worker_output=not fallback.supports_forced_tool_choice,
                    )
                else:
                    roles[role] = offline
                continue
            try:
                settings = self.configuration.model_registry.settings(
                    provider_id, model_id, require_verified=True
                )
                if settings is not None:
                    roles[role] = LangChainAgentSuite(
                        build_chat_model(settings),
                        self._skill_catalog,
                        self.configuration.agent_prompts(),
                        model_call_limit=call_limit,
                        structured_parse_retries=parse_retries,
                        force_prompt_worker_output=not settings.supports_forced_tool_choice,
                    )
                else:
                    roles[role] = offline
            except (KeyError, ValueError):
                # Keep the control plane available so the Solver page can repair
                # a deleted/stale assignment. Preflight reports it as a blocker.
                roles[role] = offline
        return RoutedAgentSuite(roles)

    def create_task(self, request: Any) -> dict[str, Any]:
        scene = self.configuration.scene(request.mode)
        policy = request.execution_policy or self._default_policy(scene)
        task = Task(
            **({"id": request.id} if getattr(request, "id", None) else {}),
            name=request.name,
            mode=request.mode,
            spec=TaskSpec(
                objective=request.objective,
                instructions=tuple(request.instructions),
                constraints=tuple(request.constraints),
                success_criteria=tuple(request.success_criteria),
                mode_options=dict(getattr(request, "mode_options", {}) or {}),
            ),
        )
        workspace = TaskWorkspace(self.run_root, task.id)
        resources: list[ResourceRef] = []
        for source_value in request.input_paths:
            source = Path(source_value)
            destination = workspace.ingest_input(source)
            resources.append(
                ResourceRef(
                    name=destination.name,
                    path=destination.relative_to(workspace.root).as_posix(),
                    media_type=workspace.media_type(destination),
                    sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                )
            )
        task = task.model_copy(
            update={
                "spec": task.spec.model_copy(update={"resources": tuple(resources)})
            }
        )
        store = TaskStore(workspace.database_path)
        try:
            store.create_task(task, policy)
            store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="TASK_CREATED",
                    payload={
                        "name": task.name,
                        "mode": task.mode,
                        "resource_count": len(resources),
                    },
                )
            )
        finally:
            store.close()
        return {
            "task_id": task.id,
            "status": task.status.value,
            "task": task.model_dump(mode="json"),
        }

    def _default_policy(self, scene: dict[str, Any]) -> ExecutionPolicy:
        """Translate the scene default into the compact persisted policy.

        HTTP clients normally send the policy returned by ``scenes.json``.
        CLI and direct Python callers use this path, so they resolve the same
        source instead of silently falling back to model-class defaults.
        """
        value = dict(scene.get("default_execution_policy") or {})
        network = dict(value.get("network") or {})
        compute = dict(value.get("local_compute") or {})
        high_impact = dict(value.get("high_impact") or {})
        allowed = set(self.configuration.runtime.tool_defaults.allowed)
        approval_required: set[str] = set()
        if compute.get("mode") == "isolated":
            allowed.add("run_command")
        return ExecutionPolicy(
            tool=ToolPolicy(
                allowed_tools=frozenset(allowed),
                approval_required=frozenset(approval_required),
                max_tool_calls=self.configuration.runtime.budget.task.max_tool_calls,
            ),
            network_access=network.get("access", "disabled"),
            allowed_origins=tuple(
                network.get("custom_origins") or network.get("seed_origins") or ()
            ),
            local_compute=compute.get("mode", "disabled"),
            high_impact_mode=high_impact.get("mode", "forbidden"),
            high_impact_allowed_actions=tuple(
                high_impact.get("allowed_actions") or ()
            ),
            command_timeout_seconds=int(
                compute.get(
                    "timeout_seconds",
                    self.configuration.runtime.kali.command_timeout_seconds,
                )
            ),
        )

    def run_task(self, task_id: str) -> dict[str, Any]:
        with self._runtime(task_id) as (store, graph):
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.COMPLETED_WITH_LIMITATIONS,
            }:
                return {
                    "task_id": task_id,
                    "status": task.status.value,
                    "interrupts": [],
                }
            try:
                result = graph.invoke(task_id)
            except TaskCancelledError:
                return {"task_id": task_id, "status": "cancelled", "interrupts": []}
            except BudgetExceededError as exc:
                preserved = self._preserve_budget_limited_result(store, task, exc)
                if preserved is not None:
                    return preserved
                self._record_failure(store, task_id, exc)
                raise
            except BaseException as exc:
                self._record_failure(store, task_id, exc)
                raise
            return self._graph_response(task_id, result)

    def resume_task(
        self, task_id: str, decision: bool | dict[str, Any]
    ) -> dict[str, Any]:
        with self._runtime(task_id) as (store, graph):
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            try:
                result = graph.resume(task_id, decision)
            except TaskCancelledError:
                return {"task_id": task_id, "status": "cancelled", "interrupts": []}
            except BudgetExceededError as exc:
                preserved = self._preserve_budget_limited_result(store, task, exc)
                if preserved is not None:
                    return preserved
                self._record_failure(store, task_id, exc)
                raise
            except BaseException as exc:
                self._record_failure(store, task_id, exc)
                raise
            return self._graph_response(task_id, result)

    def decide_tool_action(
        self,
        task_id: str,
        action_id: str,
        *,
        approved: bool,
        message: str = "",
    ) -> dict[str, Any]:
        """Persist one HITL decision and resume the exact nested Worker call."""

        with self._runtime(task_id) as (store, graph):
            action = store.get_action(action_id)
            if action is None or action.task_id != task_id:
                raise KeyError(f"pending approval not found: {action_id}")
            if action.status != "awaiting_approval":
                raise ValueError(f"tool action is not awaiting approval: {action_id}")
            status = "approved" if approved else "rejected"
            store.save_action(
                action.model_copy(
                    update={
                        "status": status,
                        "summary": message or ("Approved once." if approved else "Rejected by operator."),
                        "updated_at": utc_now(),
                    }
                )
            )
            store.set_task_status(task_id, TaskStatus.RUNNING)
            store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="ACTION_APPROVED" if approved else "ACTION_REJECTED",
                    solver_id=action.solver_id,
                    intent_id=action.intent_id,
                    payload={
                        "action_id": action.id,
                        "tool_name": action.tool_name,
                        "message": message,
                    },
                )
            )
            decision = {
                "decisions": [
                    {
                        "type": "approve" if approved else "reject",
                        "message": message,
                    }
                ]
            }
            try:
                result = graph.resume(task_id, decision)
            except TaskCancelledError:
                return {"task_id": task_id, "status": "cancelled", "interrupts": []}
            except BudgetExceededError as exc:
                task = store.get_task(task_id)
                assert task is not None
                preserved = self._preserve_budget_limited_result(store, task, exc)
                if preserved is not None:
                    return preserved
                self._record_failure(store, task_id, exc)
                raise
            except BaseException as exc:
                self._record_failure(store, task_id, exc)
                raise
            return self._graph_response(task_id, result)

    def _preserve_budget_limited_result(
        self, store: TaskStore, task: Task, exc: BudgetExceededError
    ) -> dict[str, Any] | None:
        snapshot = store.snapshot(task.id)
        confirmed = [
            item for item in snapshot["findings"] if item["status"] == "confirmed"
        ]
        if not confirmed:
            return None
        report_input = {
            **snapshot,
            "findings": confirmed,
            "evidence_claims": [
                item
                for item in snapshot["evidence_claims"]
                if item["status"] == "confirmed"
            ],
        }
        draft = ReportDraft(
            executive_summary=(
                "The task reached its configured Runtime budget after producing "
                f"{len(confirmed)} confirmed finding(s)."
            ),
            limitations=[str(exc)],
        )
        markdown = render_markdown(report_input, draft)
        workspace = TaskWorkspace(self.run_root, task.id)
        path = workspace.reports / "report.md"
        path.write_text(markdown, encoding="utf-8")
        relative = path.relative_to(workspace.root).as_posix()
        store.save_report(task.id, markdown, relative)
        store.set_task_status(task.id, TaskStatus.COMPLETED_WITH_LIMITATIONS)
        store.append_event(
            AgentEvent(
                task_id=task.id,
                type="REPORT_GENERATED",
                solver_id="runtime",
                payload={
                    "path": relative,
                    "partial": True,
                    "reason": str(exc),
                    "model_calls": 0,
                },
            )
        )
        store.append_event(
            AgentEvent(
                task_id=task.id,
                type="TASK_COMPLETED_WITH_LIMITATIONS",
                payload={
                    "report_path": relative,
                    "confirmed_findings": len(confirmed),
                    "reason": str(exc),
                },
            )
        )
        return {
            "task_id": task.id,
            "status": TaskStatus.COMPLETED_WITH_LIMITATIONS.value,
            "interrupts": [],
        }

    @staticmethod
    def _record_failure(store: TaskStore, task_id: str, exc: BaseException) -> None:
        active_intents = [
            intent
            for intent in store.list_intents(task_id)
            if intent.status in {IntentStatus.RUNNING, IntentStatus.REVIEW}
        ]
        for intent in active_intents:
            store.update_intent(
                intent.model_copy(
                    update={"status": IntentStatus.FAILED, "updated_at": utc_now()}
                )
            )
            store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="INTENT_BLOCKED",
                    solver_id=intent.assigned_solver_id,
                    intent_id=intent.id,
                    payload={
                        "status": "failed",
                        "reason": str(exc)[:2000],
                        "error_type": type(exc).__name__,
                    },
                )
            )
        latest_active = next(
            (
                event
                for event in reversed(store.list_events(task_id, limit=1000))
                if event.type == "SOLVER_STATUS_CHANGED"
                and event.payload.get("status") == "running"
            ),
            None,
        )
        if latest_active and latest_active.solver_id:
            store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="SOLVER_STATUS_CHANGED",
                    solver_id=latest_active.solver_id,
                    intent_id=latest_active.intent_id,
                    payload={
                        "status": "failed",
                        "summary": str(exc)[:1000],
                        "stage": latest_active.solver_id,
                    },
                )
            )
        store.set_task_status(task_id, TaskStatus.FAILED)
        store.append_event(
            AgentEvent(
                task_id=task_id,
                type="TASK_FAILED",
                payload={
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "current_intent_id": active_intents[0].id
                    if active_intents
                    else None,
                    "solver_id": latest_active.solver_id if latest_active else None,
                    "model_calls": _failed_model_calls(exc),
                },
            )
        )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._store(task_id) as store:
            store.set_task_status(task_id, TaskStatus.CANCELLED)
            store.append_event(AgentEvent(task_id=task_id, type="TASK_CANCELLED"))
        return {"task_id": task_id, "status": "cancelled"}

    def record_intervention(
        self, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._store(task_id) as store:
            event = store.append_event(
                AgentEvent(task_id=task_id, type="USER_INTERVENTION", payload=payload)
            )
        return {
            "task_id": task_id,
            "accepted": True,
            "status": "recorded",
            "intervention": {"id": f"evt-{event.seq}"},
        }

    def send_solver_message(
        self,
        task_id: str,
        solver_id: str,
        *,
        content: str,
        attachments: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """Persist a solver-scoped prompt and make uploaded files task resources."""
        if solver_id not in {"supervisor", "worker", "reviewer", "reporter"}:
            raise ValueError(f"unknown solver: {solver_id}")
        content = content.strip()
        if not content and not attachments:
            raise ValueError("content or attachments are required")
        workspace = TaskWorkspace(self.run_root, task_id)
        ingested: list[dict[str, Any]] = []
        should_resume = False
        with self._store(task_id) as store:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            for item in attachments:
                source = Path(str(item["path"]))
                destination = workspace.ingest_input(source)
                raw = destination.read_bytes()
                ingested.append(
                    {
                        "name": str(item.get("name") or destination.name),
                        "path": destination.relative_to(workspace.root).as_posix(),
                        "media_type": str(item.get("media_type") or workspace.media_type(destination)),
                        "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
                source.unlink(missing_ok=True)
            intent_id = next(
                (
                    event.intent_id
                    for event in reversed(store.list_events(task_id, limit=1000))
                    if event.solver_id == solver_id and event.intent_id
                ),
                None,
            )
            event = store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="USER_SOLVER_MESSAGE",
                    solver_id=solver_id,
                    intent_id=intent_id,
                    payload={
                        "kind": "answer" if task.status == TaskStatus.AWAITING_USER_INPUT else "instruction",
                        "scope": "solver",
                        "target_id": solver_id,
                        "content": content[:12000],
                        "attachments": ingested,
                    },
                )
            )
            should_resume = (
                task.status == TaskStatus.AWAITING_USER_INPUT
                and solver_id == "supervisor"
                and bool(content)
            )
        if should_resume:
            result = self.resume_task(task_id, {"content": content})
            return {**result, "accepted": True, "message_id": f"evt-{event.seq}"}
        return {
            "task_id": task_id,
            "accepted": True,
            "status": "recorded",
            "message_id": f"evt-{event.seq}",
            "attachments": ingested,
        }

    def control_solver(self, task_id: str, solver_id: str, action: str) -> dict[str, Any]:
        if solver_id not in {"supervisor", "worker", "reviewer", "reporter"}:
            raise ValueError(f"unknown solver: {solver_id}")
        if action not in {"pause", "resume"}:
            raise ValueError("action must be pause or resume")
        with self._store(task_id) as store:
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="SOLVER_CONTROL_CHANGED",
                    solver_id=solver_id,
                    payload={
                        "state": "paused" if action == "pause" else "running",
                        "action": action,
                        "summary": (
                            "已请求在下一个 LangGraph 检查点暂停"
                            if action == "pause"
                            else "已恢复 Solver 后续工作"
                        ),
                    },
                )
            )
        resumed = False
        if action == "resume":
            with self._runtime(task_id) as (_store, graph):
                checkpoint = graph.state(task_id)
                requests = [
                    getattr(item, "value", item)
                    for item in (getattr(checkpoint, "interrupts", ()) or ())
                ]
                if any(
                    isinstance(item, dict)
                    and item.get("kind") == "solver_pause"
                    and item.get("solver_id") == solver_id
                    for item in requests
                ):
                    graph.resume(
                        task_id,
                        {"action": "resume_solver", "solver_id": solver_id},
                    )
                    resumed = True
        return {
            "task_id": task_id,
            "solver_id": solver_id,
            "accepted": True,
            "status": "paused" if action == "pause" else "running",
            "resumed_checkpoint": resumed,
        }

    def set_solver_model(
        self, task_id: str, solver_id: str, provider_id: str, model_id: str
    ) -> dict[str, Any]:
        if solver_id not in {"supervisor", "worker", "reviewer", "reporter"}:
            raise ValueError(f"unknown solver: {solver_id}")
        self.configuration.model_registry.settings(
            provider_id, model_id, require_verified=True
        )
        provider = self.configuration.model_registry.provider(provider_id)
        model = provider.model(model_id)
        snapshot = {
            "provider_id": provider_id,
            "provider_name": provider.name,
            "model_id": model_id,
            "model_name": model.name,
            "verification_status": model.verification_status,
            "ready": True,
        }
        with self._store(task_id) as store:
            store.append_event(
                AgentEvent(
                    task_id=task_id,
                    type="SOLVER_MODEL_CHANGED",
                    solver_id=solver_id,
                    payload={"model": snapshot, "summary": f"后续调用改用 {provider.name} / {model.name}"},
                )
            )
        return {"task_id": task_id, "solver_id": solver_id, "model": snapshot}

    @staticmethod
    def _model_overrides(store: TaskStore, task_id: str) -> dict[str, tuple[str, str]]:
        values: dict[str, tuple[str, str]] = {}
        for event in store.list_events(task_id, limit=1000):
            if event.type != "SOLVER_MODEL_CHANGED" or not event.solver_id:
                continue
            model = event.payload.get("model") or {}
            provider_id = str(model.get("provider_id") or "")
            model_id = str(model.get("model_id") or "")
            if provider_id and model_id:
                values[event.solver_id] = (provider_id, model_id)
        return values

    def delete_task(self, task_id: str) -> dict[str, Any]:
        workspace = TaskWorkspace(self.run_root, task_id)
        if not workspace.database_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        target = workspace.root.resolve()
        target.relative_to(self.run_root)
        shutil.rmtree(target)
        return {"task_id": task_id, "deleted": True}

    def snapshot(self, task_id: str) -> dict[str, Any]:
        with self._store(task_id) as store:
            raw = store.snapshot(task_id)
            events = [
                item.model_dump(mode="json")
                for item in store.list_events(task_id, limit=1000)
            ]
            return runtime_snapshot_projection(raw, events, self.configuration.runtime)

    def events(
        self, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._store(task_id) as store:
            return [
                event_projection(event.model_dump(mode="json"))
                for event in store.list_events(
                    task_id, after_seq=after_seq, limit=limit
                )
            ]

    def report(self, task_id: str) -> dict[str, str] | None:
        with self._store(task_id) as store:
            return store.get_report(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for database in self.run_root.glob("*/state.db"):
            store = TaskStore(database)
            try:
                task_id = database.parent.name
                task = store.get_task(task_id)
                if task is not None:
                    raw = store.snapshot(task_id)
                    events = [
                        item.model_dump(mode="json")
                        for item in store.list_events(task_id, limit=1000)
                    ]
                    tasks.append(
                        task_list_projection(
                            runtime_snapshot_projection(
                                raw, events, self.configuration.runtime
                            )
                        )
                    )
            finally:
                store.close()
        return sorted(tasks, key=lambda item: item["created_at"], reverse=True)

    @contextmanager
    def _runtime(self, task_id: str) -> Iterator[tuple[TaskStore, TaskGraph]]:
        workspace = TaskWorkspace(self.run_root, task_id)
        if not workspace.database_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        store = TaskStore(workspace.database_path)
        task = store.get_task(task_id)
        if task is None:
            store.close()
            raise KeyError(f"task not found: {task_id}")
        graph = TaskGraph(
            RuntimeDeps(
                store=store,
                workspace=workspace,
                agents=self._agents_for_task(task, store),
                agents_factory=(
                    None
                    if self._agents_overridden
                    else lambda: self._agents_for_task(task, store)
                ),
                sandbox_image=self.configuration.runtime.sandbox_image,
                configuration=self.configuration,
                skills=self.skills,
                external_tools=self.external_tools,
            )
        )
        try:
            yield store, graph
        finally:
            graph.close()
            store.close()

    @contextmanager
    def _store(self, task_id: str) -> Iterator[TaskStore]:
        workspace = TaskWorkspace(self.run_root, task_id)
        if not workspace.database_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        store = TaskStore(workspace.database_path)
        try:
            yield store
        finally:
            store.close()

    @staticmethod
    def _graph_response(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
        state = result.get("state") or {}
        interrupts = result.get("interrupts") or []
        interrupt_kinds = {
            item.get("kind") for item in interrupts if isinstance(item, dict)
        }
        status = (
            "awaiting_user_input"
            if "user_input" in interrupt_kinds
            else "paused"
            if "solver_pause" in interrupt_kinds
            else "awaiting_approval"
            if interrupts
            else state.get("status", "running")
        )
        return {
            "task_id": task_id,
            "status": status,
            "interrupts": interrupts,
            "state": state,
        }


__all__ = ["TaskRuntimeService"]


def _failed_model_calls(exc: BaseException) -> int:
    recorded = getattr(exc, "model_calls", None)
    if recorded is not None:
        return int(recorded)
    match = re.search(r"run limit \((\d+)/(\d+)\)", str(exc), re.IGNORECASE)
    return int(match.group(1)) if match else 0
