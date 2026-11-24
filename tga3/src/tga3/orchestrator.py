"""Pluggable execution-mode orchestrator protocol and registry.

Adding a new execution mode only requires implementing TaskOrchestrator (or subclassing
BaseTaskOrchestrator) and registering it with `coordinator.orchestrators.register(...)`,
without scattering `if scene_id == ...` branches across coordinator, routes, or automation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from .aoe_models import active_attempts, attempt_budgets
from .config import ResolvedAgent
from .domain import (
    SYSTEM_ACTOR,
    USER_ACTOR,
    AgentRun,
    AgentState,
    EntryKind,
    EventKind,
    PublishRequest,
    RunState,
    TaskRun,
)
from .errors import ConflictError
from .knowledge import KnowledgeSource
from .provider_profiles import ProviderProtocol
from .token_usage import COUNTERS, TokenUsageTracker

if TYPE_CHECKING:
    from .aoe import AOERuntime
    from .coordinator import TaskCoordinator


@dataclass(frozen=True)
class TaskCreateOptions:
    knowledge_source: KnowledgeSource = "online"
    produce_report: bool = True
    aoe_attempt_minutes: int = 20
    aoe_first_attempt_minutes: int | None = None
    aoe_second_attempt_minutes: int | None = None
    aoe_joint_minutes: int | None = None
    aoe_auto_submit_flag: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskRestartOptions:
    rerun_scout: bool = False
    retry_abandoned: bool = False
    aoe_first_attempt_minutes: int | None = None
    aoe_second_attempt_minutes: int | None = None
    aoe_joint_minutes: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class TaskOrchestrator(Protocol):
    """Strategy interface for a task execution mode (single-task, AOE, or future modes)."""

    mode_id: str
    auto_report_on_blackboard_change: bool

    def matches_scene(self, scene_id: str) -> bool: ...
    def matches_task(self, task: TaskRun) -> bool: ...
    def build_initial_metadata(self, options: TaskCreateOptions) -> dict[str, Any]: ...
    async def run_startup(self, task_id: UUID, bindings: dict[str, ResolvedAgent]) -> None: ...
    async def restore(self) -> None: ...
    async def on_cancel_startup(self, task: TaskRun) -> None: ...
    async def on_stop(self, task: TaskRun) -> None: ...
    async def before_delete(self, task: TaskRun) -> None: ...
    def after_delete(self, task: TaskRun) -> None: ...
    def reset_agent_metadata_on_restart(self) -> dict[str, Any] | None: ...
    async def restart(
        self,
        task: TaskRun,
        *,
        existing_runs: dict[str, AgentRun],
        resolved_agents: dict[str, ResolvedAgent],
        options: TaskRestartOptions,
        restart_count: int,
        fallback_models: list[str],
    ) -> dict[str, Any]: ...
    async def pause_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None: ...
    async def resume_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None: ...
    async def set_agent_model(
        self,
        task: TaskRun,
        agent_id: str,
        provider_id: str,
        model_id: str,
        protocol: ProviderProtocol,
    ) -> AgentRun | None: ...
    async def on_realtime_prompt(self, task: TaskRun, text: str, idempotency_key: str) -> None: ...
    async def refresh_token_usage(self, task: TaskRun, usage: TokenUsageTracker) -> None: ...
    async def on_agent_hello(self, task: TaskRun, agent_id: str) -> None: ...
    async def on_agent_status(self, task: TaskRun, run: AgentRun, state: AgentState) -> None: ...
    async def on_action_started(self, task_id: UUID, run: AgentRun, params: dict[str, Any]) -> None: ...


class BaseTaskOrchestrator:
    """Default no-op implementations for optional lifecycle hooks."""

    mode_id: str = "default"
    auto_report_on_blackboard_change: bool = True

    def __init__(self, coordinator: TaskCoordinator) -> None:
        self.coordinator = coordinator

    def matches_scene(self, scene_id: str) -> bool:
        return scene_id == self.mode_id

    def matches_task(self, task: TaskRun) -> bool:
        return self.matches_scene(task.scene_id)

    def build_initial_metadata(self, options: TaskCreateOptions) -> dict[str, Any]:
        return {"knowledge_source": options.knowledge_source, "produce_report": options.produce_report}

    async def restore(self) -> None:
        return None

    async def on_cancel_startup(self, task: TaskRun) -> None:
        return None

    async def on_stop(self, task: TaskRun) -> None:
        return None

    async def before_delete(self, task: TaskRun) -> None:
        return None

    def after_delete(self, task: TaskRun) -> None:
        return None

    def reset_agent_metadata_on_restart(self) -> dict[str, Any] | None:
        return None

    async def pause_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None:
        return None

    async def resume_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None:
        return None

    async def set_agent_model(
        self,
        task: TaskRun,
        agent_id: str,
        provider_id: str,
        model_id: str,
        protocol: ProviderProtocol,
    ) -> AgentRun | None:
        return None

    async def on_realtime_prompt(self, task: TaskRun, text: str, idempotency_key: str) -> None:
        return None

    async def refresh_token_usage(self, task: TaskRun, usage: TokenUsageTracker) -> None:
        return None

    async def on_agent_hello(self, task: TaskRun, agent_id: str) -> None:
        return None

    async def on_agent_status(self, task: TaskRun, run: AgentRun, state: AgentState) -> None:
        return None

    async def on_action_started(self, task_id: UUID, run: AgentRun, params: dict[str, Any]) -> None:
        return None


class SingleTaskOrchestrator(BaseTaskOrchestrator):
    """Default single-challenge mode: 3-round Scout -> parallel Workers -> Reporter."""

    mode_id = "single"
    auto_report_on_blackboard_change = True

    def matches_scene(self, scene_id: str) -> bool:
        return True

    async def run_startup(self, task_id: UUID, bindings: dict[str, ResolvedAgent]) -> None:
        await self.coordinator._scout_then_workers(task_id, bindings)

    async def restart(
        self,
        task: TaskRun,
        *,
        existing_runs: dict[str, AgentRun],
        resolved_agents: dict[str, ResolvedAgent],
        options: TaskRestartOptions,
        restart_count: int,
        fallback_models: list[str],
    ) -> dict[str, Any]:
        c = self.coordinator
        task_id = task.id
        scout_run = existing_runs.get("scout")
        scout_done = bool(scout_run and scout_run.metadata.get("scout_phase") == "completed")
        entries = await c.storage.list_entries(task_id, limit=10_000)
        has_final = any(entry.kind == EntryKind.FINAL_CANDIDATE for entry in entries)
        automation = c.automation

        if has_final and task.state == RunState.FAILED and not options.rerun_scout and automation is not None:
            resumed_from = "reporter"
            await c.storage.update_agent(
                task_id,
                "reporter",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.CREATED,
                container_id=None,
                last_error=None,
            )
            refreshed = await c.storage.update_task(
                task_id, state=RunState.REPORTING, metadata={"restart_count": restart_count}
            )
            await c.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：已有最终候选，直接重跑 Reporter 生成报告。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )
            automation.start_report(task_id)
        elif scout_done and not options.rerun_scout:
            resumed_from = "workers"
            refreshed = await c.storage.update_task(
                task_id, state=RunState.RUNNING, metadata={"restart_count": restart_count}
            )
            await c.blackboard.publish(
                task_id,
                SYSTEM_ACTOR,
                PublishRequest(
                    kind=EntryKind.USER_PROMPT,
                    topic="restart",
                    body={
                        "text": (
                            f"任务已重新启动（第 {restart_count} 次运行）。"
                            "此前黑板中已验证的 Intel、Findings 与工作区文件均已保留，"
                            "请先查阅已有进展并确认目标状态后继续推进，避免重复劳动。"
                        )
                    },
                    idempotency_key=f"task-restart:{restart_count}",
                ),
            )
            await c.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：保留已有 Intel 与黑板，直接启动 Workers 续跑。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )

            async def launch_only() -> None:
                try:
                    await c._launch_workers(
                        task_id,
                        resolved_agents,
                        knowledge_source=refreshed.metadata.get("knowledge_source", "online"),
                        fresh_session=True,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await c.storage.update_task(task_id, state=RunState.FAILED)
                    await c.events.emit(
                        task_id, actor=SYSTEM_ACTOR, kind=EventKind.ERROR, text=f"任务重启失败：{exc}"
                    )

            c._startup_jobs[task_id] = asyncio.create_task(launch_only())
        else:
            resumed_from = "scout"
            await c.storage.update_agent(
                task_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.CREATED,
                container_id=None,
                last_error=None,
                metadata={"scout_phase": "executing"},
            )
            refreshed = await c.storage.update_task(
                task_id, state=RunState.SCOUTING, metadata={"restart_count": restart_count}
            )
            if entries:
                await c.blackboard.publish(
                    task_id,
                    SYSTEM_ACTOR,
                    PublishRequest(
                        kind=EntryKind.USER_PROMPT,
                        topic="restart",
                        body={
                            "text": (
                                f"任务已重新启动（第 {restart_count} 次运行），将从 Scout 阶段重新执行。"
                                "此前黑板记录与附件已保留。"
                            )
                        },
                        idempotency_key=f"task-restart:{restart_count}",
                    ),
                )
            await c.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：从 Scout 阶段重新开始。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )
            c._startup_jobs[task_id] = asyncio.create_task(c._scout_then_workers(task_id, resolved_agents))

        return {
            "task": refreshed.model_dump(mode="json"),
            "resumed_from": resumed_from,
            "restart_count": restart_count,
            "fallback_models": fallback_models,
        }


class AOEOrchestrator(BaseTaskOrchestrator):
    """AOE multi-question batch mode delegating to AOERuntime."""

    mode_id = "aoe"
    auto_report_on_blackboard_change = False

    def __init__(self, coordinator: TaskCoordinator, aoe: AOERuntime) -> None:
        super().__init__(coordinator)
        self.aoe = aoe

    def matches_scene(self, scene_id: str) -> bool:
        return scene_id == "aoe"

    def build_initial_metadata(self, options: TaskCreateOptions) -> dict[str, Any]:
        stage_minutes = {
            "first": options.aoe_first_attempt_minutes,
            "second": options.aoe_second_attempt_minutes,
            "joint": options.aoe_joint_minutes,
        }
        if any(
            not 1 <= value <= 1440
            for value in [options.aoe_attempt_minutes, *stage_minutes.values()]
            if value is not None
        ):
            raise ValueError("AOE attempt budgets must be between 1 and 1440 minutes")
        budget_metadata = (
            {
                "aoe_attempt_budgets": {
                    stage: (minutes if minutes is not None else options.aoe_attempt_minutes) * 60
                    for stage, minutes in stage_minutes.items()
                }
            }
            if any(value is not None for value in stage_minutes.values())
            else {"aoe_attempt_seconds": options.aoe_attempt_minutes * 60}
        )
        return {
            **super().build_initial_metadata(options),
            **budget_metadata,
            "aoe_phase": "collecting",
            "aoe_auto_submit_flag": options.aoe_auto_submit_flag,
            "aoe_scout_mode": "collection_only",
            "aoe_finish_confirm_enabled": True,
            "scout_complete": False,
        }

    async def run_startup(self, task_id: UUID, bindings: dict[str, ResolvedAgent]) -> None:
        await self.aoe.run(task_id, bindings)

    async def restore(self) -> None:
        c = self.coordinator
        await self.aoe.restore_paths()
        for parent in await c.storage.list_tasks(limit=10000):
            if parent.scene_id != "aoe" or parent.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                continue
            bindings = {
                run.agent_id: c.config.resolve_agent(
                    run.agent_id, run.provider_id, run.model_id, run.protocol, runtime=run.sdk or None
                )
                for run in await c.storage.list_agents(parent.id)
            }
            c._startup_jobs[parent.id] = asyncio.create_task(self.aoe.run(parent.id, bindings))

    async def on_cancel_startup(self, task: TaskRun) -> None:
        if task.parent_task_id is not None:
            return
        # Shutdown preserves current attempt deadlines/history for same-question recovery.
        for question in await self.coordinator.storage.list_questions(task.id):
            await asyncio.gather(*(self.aoe.stop_worker(question.id, a["agent_id"]) for a in active_attempts(question)))
            if self.aoe.automation is not None:
                await self.aoe.automation.cancel(question.id)
            await self.coordinator.containers.stop(question.id, "reporter")

    async def on_stop(self, task: TaskRun) -> None:
        if task.parent_task_id is not None:
            return
        await self.aoe.stop_children(task.id, cancelled=True)
        await self.coordinator.storage.update_task(task.id, metadata={"aoe_phase": "cancelled"})

    async def before_delete(self, task: TaskRun) -> None:
        if task.parent_task_id is not None:
            return
        if not await self.aoe.targets.release_parent(task.id):
            raise ConflictError("Target cleanup is pending; retry deletion after platform close succeeds")

    def after_delete(self, task: TaskRun) -> None:
        if task.parent_task_id is None:
            self.aoe.targets.forget_parent(task.id)

    def reset_agent_metadata_on_restart(self) -> dict[str, Any] | None:
        return {"aoe_paused": False}

    async def restart(
        self,
        task: TaskRun,
        *,
        existing_runs: dict[str, AgentRun],
        resolved_agents: dict[str, ResolvedAgent],
        options: TaskRestartOptions,
        restart_count: int,
        fallback_models: list[str],
    ) -> dict[str, Any]:
        c = self.coordinator
        stage_minutes = {
            "first": options.aoe_first_attempt_minutes,
            "second": options.aoe_second_attempt_minutes,
            "joint": options.aoe_joint_minutes,
        }
        if any(not 1 <= value <= 1440 for value in stage_minutes.values() if value is not None):
            raise ValueError("AOE attempt budgets must be between 1 and 1440 minutes")
        new_budgets = None
        if any(value is not None for value in stage_minutes.values()):
            current_budgets = attempt_budgets(task.metadata)
            new_budgets = {
                stage: (minutes * 60 if minutes is not None else current_budgets[stage])
                for stage, minutes in stage_minutes.items()
            }
        summary = await self.aoe.prepare_restart(
            task.id,
            rerun_scout=options.rerun_scout,
            retry_abandoned=options.retry_abandoned,
            budgets=new_budgets,
            restart_count=restart_count,
        )
        detail_parts = ["重跑 Scout 收集" if summary["need_scout"] else "跳过 Scout"]
        if summary["resumed_questions"]:
            detail_parts.append(f"续跑 {summary['resumed_questions']} 道未完成题")
        if summary["requeued_abandoned"]:
            detail_parts.append(f"重新挑战 {summary['requeued_abandoned']} 道已放弃题")
        if summary["pending_reports"]:
            detail_parts.append(f"补跑 {summary['pending_reports']} 份报告")
        await c.events.emit(
            task.id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text=f"任务已原地重启（第 {restart_count} 次运行）：{'，'.join(detail_parts)}。",
            payload={"restart_count": restart_count, **summary, "fallback_models": fallback_models},
        )
        c._startup_jobs[task.id] = asyncio.create_task(self.aoe.run(task.id, resolved_agents))
        refreshed = await c.storage.get_task(task.id)
        return {
            "task": refreshed.model_dump(mode="json"),
            "resumed_from": summary["resumed_from"],
            "restart_count": restart_count,
            "fallback_models": fallback_models,
            "summary": summary,
        }

    async def pause_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None:
        if task.parent_task_id is not None:
            return None
        c = self.coordinator
        slot = await c._worker_run(task.id, agent_id)
        await c.storage.update_agent(task.id, agent_id, metadata={"aoe_paused": True})
        if slot.metadata.get("current_question_id"):
            await c.pause_agent(UUID(slot.metadata["current_question_id"]), agent_id)
        return await c.storage.update_agent(
            task.id, agent_id, desired_state=AgentState.PAUSED, actual_state=AgentState.PAUSED
        )

    async def resume_agent(self, task: TaskRun, agent_id: str) -> AgentRun | None:
        if task.parent_task_id is not None:
            return None
        c = self.coordinator
        slot = await c._worker_run(task.id, agent_id)
        await c.storage.update_agent(task.id, agent_id, metadata={"aoe_paused": False})
        if slot.metadata.get("current_question_id"):
            await c.resume_agent(UUID(slot.metadata["current_question_id"]), agent_id)
        return await c.storage.update_agent(
            task.id, agent_id, desired_state=AgentState.RUNNING, actual_state=AgentState.IDLE
        )

    async def set_agent_model(
        self,
        task: TaskRun,
        agent_id: str,
        provider_id: str,
        model_id: str,
        protocol: ProviderProtocol,
    ) -> AgentRun | None:
        if task.parent_task_id is not None:
            return None
        c = self.coordinator
        slot = await c._worker_run(task.id, agent_id)
        c.config.resolve_agent(agent_id, provider_id, model_id, protocol, runtime=slot.sdk or None)
        if slot.metadata.get("current_question_id"):
            await c.set_agent_model(
                UUID(slot.metadata["current_question_id"]), agent_id, provider_id, model_id, protocol
            )
        return await c.storage.update_agent(
            task.id, agent_id, provider_id=provider_id, model_id=model_id, protocol=protocol
        )

    async def on_realtime_prompt(self, task: TaskRun, text: str, idempotency_key: str) -> None:
        if task.parent_task_id is not None:
            return
        for question in await self.coordinator.storage.list_questions(task.id):
            if question.state in {RunState.SCOUTING, RunState.RUNNING}:
                await self.coordinator.blackboard.publish(
                    question.id,
                    USER_ACTOR,
                    PublishRequest(
                        kind="user_prompt",
                        topic="realtime",
                        body={"text": text},
                        idempotency_key=f"aoe-realtime:{idempotency_key}",
                    ),
                )

    async def refresh_token_usage(self, task: TaskRun, usage: TokenUsageTracker) -> None:
        if task.parent_task_id is not None:
            return
        c = self.coordinator
        totals: dict[str, dict[str, int]] = {}
        for question in await c.storage.list_questions(task.id):
            await usage.refresh_task(question.id)
            for agent in await c.storage.list_agents(question.id):
                if agent.agent_id == "scout":
                    continue  # Scout has one shared native session under the parent workspace.
                current = agent.metadata.get("token_usage")
                if current and current.get("reported"):
                    value = totals.setdefault(agent.agent_id, {key: 0 for key in COUNTERS})
                    for key in COUNTERS:
                        value[key] += current.get(key, 0)
        for agent_id, total in totals.items():
            current = await c.storage.get_agent(task.id, agent_id)
            if any(current.metadata.get("token_usage", {}).get(key) != total[key] for key in COUNTERS):
                await c.storage.update_agent(
                    task.id,
                    agent_id,
                    metadata={
                        "token_usage": {
                            **total,
                            "reported": True,
                            "updated_at": task.updated_at.isoformat(),
                        }
                    },
                )

    async def on_agent_hello(self, task: TaskRun, agent_id: str) -> None:
        if not task.parent_task_id:
            return
        active = next((a for a in active_attempts(task) if a["agent_id"] == agent_id), None)
        if active and active.get("deadline") is not None:
            await self.coordinator.gateway.notify(
                task.id, agent_id, "session.deadline", {"attempt_id": active["id"], "deadline": active["deadline"]}
            )

    async def on_agent_status(self, task: TaskRun, run: AgentRun, state: AgentState) -> None:
        if run.metadata.get("aoe_attempt_id") and state in {AgentState.PAUSED, AgentState.RUNNING}:
            await self.aoe.record_pause(task.id, run.agent_id, paused=state == AgentState.PAUSED)

    async def on_action_started(self, task_id: UUID, run: AgentRun, params: dict[str, Any]) -> None:
        if params.get("tool_use_id") and run.metadata.get("aoe_attempt_id"):
            # Native built-in web search has no PreToolUse hook.
            await self.aoe.action_started(task_id, run.agent_id, run.metadata["aoe_attempt_id"])


class OrchestratorRegistry:
    """Ordered registry of execution modes with a fallback default orchestrator."""

    def __init__(self, default: TaskOrchestrator) -> None:
        self._default = default
        self._modes: list[TaskOrchestrator] = []

    def register(self, orchestrator: TaskOrchestrator) -> None:
        self._modes = [item for item in self._modes if item.mode_id != orchestrator.mode_id]
        self._modes.append(orchestrator)

    @property
    def all(self) -> list[TaskOrchestrator]:
        return [*self._modes, self._default]

    def for_scene(self, scene_id: str) -> TaskOrchestrator:
        for orchestrator in self._modes:
            if orchestrator.matches_scene(scene_id):
                return orchestrator
        return self._default

    def for_task(self, task: TaskRun) -> TaskOrchestrator:
        for orchestrator in self._modes:
            if orchestrator.matches_task(task):
                return orchestrator
        return self._default


__all__ = [
    "AOEOrchestrator",
    "BaseTaskOrchestrator",
    "OrchestratorRegistry",
    "SingleTaskOrchestrator",
    "TaskCreateOptions",
    "TaskOrchestrator",
    "TaskRestartOptions",
]
