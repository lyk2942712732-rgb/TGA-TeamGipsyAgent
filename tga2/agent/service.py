"""Public task service shared by API, CLI and tests."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from tga2.agent.graph import RuntimeDeps, TaskGraph
from tga2.agent.roles import AgentSuite, LangChainAgentSuite, OfflineAgentSuite
from tga2.config import Configuration
from tga2.core.models import AgentEvent, ResourceRef, Task, TaskSpec, TaskStatus
from tga2.core.policy import ExecutionPolicy
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
        self.skills = SkillRepository(self.run_root / ".config" / "skills")
        settings = model_settings or self.configuration.model
        self.configuration.model = settings
        self._agents_overridden = agents is not None
        self.agents = agents or (
            LangChainAgentSuite(
                build_chat_model(settings),
                self._select_skills,
                self.configuration.agent_prompts(),
            )
            if settings.can_call_model and settings.verified
            else OfflineAgentSuite()
        )
        self.external_tools = list(external_tools)

    def configure_model(self, settings: ModelSettings) -> dict[str, Any]:
        self.configuration.save_model(settings)
        self._agents_overridden = False
        self.agents = (
            LangChainAgentSuite(
                build_chat_model(settings),
                self._select_skills,
                self.configuration.agent_prompts(),
            )
            if settings.can_call_model and settings.verified
            else OfflineAgentSuite()
        )
        return {
            "configured": settings.can_call_model,
            "provider": settings.provider,
            "model": settings.model,
            "offline": not (settings.can_call_model and settings.verified),
        }

    def reload_configuration(self) -> None:
        if isinstance(self.agents, LangChainAgentSuite):
            self.agents.prompts = self.configuration.agent_prompts()

    def set_external_tools(self, tools: Sequence[BaseTool]) -> None:
        self.external_tools = list(tools)

    def _select_skills(self, task: Task):
        selected = task.spec.selected_skill_names
        return self.skills.select(
            task.spec.objective,
            selected_names=list(selected) if selected is not None else None,
        )

    def create_task(self, request: Any) -> dict[str, Any]:
        policy = request.execution_policy or ExecutionPolicy()
        task = Task(
            **({"id": request.id} if getattr(request, "id", None) else {}),
            name=request.name,
            mode=request.mode,
            spec=TaskSpec(
                objective=request.objective,
                instructions=tuple(request.instructions),
                constraints=tuple(request.constraints),
                success_criteria=tuple(request.success_criteria),
                selected_skill_names=tuple(request.selected_skills)
                if request.selected_skills is not None
                else None,
                agent_models=dict(getattr(request, "agent_models", {}) or {}),
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

    def run_task(self, task_id: str) -> dict[str, Any]:
        with self._runtime(task_id) as (store, graph):
            task = store.get_task(task_id)
            if task is None:
                raise KeyError(f"task not found: {task_id}")
            if task.status == TaskStatus.COMPLETED:
                return {"task_id": task_id, "status": "completed", "interrupts": []}
            try:
                result = graph.invoke(task_id)
            except BaseException as exc:
                store.set_task_status(task_id, TaskStatus.FAILED)
                store.append_event(
                    AgentEvent(
                        task_id=task_id,
                        type="TASK_FAILED",
                        payload={
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:2000],
                        },
                    )
                )
                raise
            return self._graph_response(task_id, result)

    def resume_task(
        self, task_id: str, decision: bool | dict[str, Any]
    ) -> dict[str, Any]:
        with self._runtime(task_id) as (store, graph):
            if store.get_task(task_id) is None:
                raise KeyError(f"task not found: {task_id}")
            result = graph.resume(task_id, decision)
            return self._graph_response(task_id, result)

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
            return runtime_snapshot_projection(raw, events)

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
                        task_list_projection(runtime_snapshot_projection(raw, events))
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
        graph = TaskGraph(
            RuntimeDeps(
                store=store,
                workspace=workspace,
                agents=self.agents,
                sandbox_image=self.configuration.runtime.sandbox_image,
                configuration=self.configuration,
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
        status = "awaiting_approval" if interrupts else state.get("status", "running")
        return {
            "task_id": task_id,
            "status": status,
            "interrupts": interrupts,
            "state": state,
        }


__all__ = ["TaskRuntimeService"]
