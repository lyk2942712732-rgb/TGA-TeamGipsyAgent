"""Thin task lifecycle coordinator; planning remains inside the agents."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .aoe import AOERuntime
from .aoe_models import attempt_budgets
from .blackboard import Blackboard
from .config import ResolvedAgent, TGA3Config
from .docker_runtime import ContainerRuntime, LaunchSpec
from .domain import (
    SYSTEM_ACTOR,
    USER_ACTOR,
    Actor,
    AgentRun,
    AgentState,
    EntryKind,
    EventKind,
    InputFile,
    PublishRequest,
    RunState,
    UserFileBody,
)
from .errors import ConflictError, ContractError
from .events import TaskEvents
from .gateway import AgentGateway
from .knowledge import KnowledgeSource
from .local_knowledge import LocalKnowledge
from .provider_profiles import ProviderProtocol
from .scout_intel import ScoutIntelStore
from .storage import Storage


class TaskCoordinator:
    def __init__(self, config: TGA3Config, storage: Storage, containers: ContainerRuntime) -> None:
        self.config = config
        self.storage = storage
        self.containers = containers
        self._startup_jobs: dict[UUID, asyncio.Task[None]] = {}
        self.gateway = AgentGateway(self.handle_agent_event)
        self.events = TaskEvents(storage)
        self.blackboard = Blackboard(storage, self.blackboard_changed)
        self.scout_intel = ScoutIntelStore(config, self.blackboard)
        self.change_handlers: list[Callable[[UUID, int], Awaitable[None]]] = []
        self.aoe = AOERuntime(self)

    def actor_for(self, run: AgentRun) -> Actor:
        configured = self.config.agents.agents[run.agent_id]
        return Actor(
            agent_id=run.agent_id,
            display_name=configured.display_name,
            role=configured.role,
            sdk=run.sdk,
            model=run.model_id,
        )

    async def create_and_start(
        self,
        title: str,
        initial_prompt: str,
        scene_id: str,
        initial_files: list[tuple[str, str, bytes]] | None = None,
        task_id: UUID | None = None,
        model_overrides: dict[str, tuple[str, str, ProviderProtocol]] | None = None,
        aoe_attempt_minutes: int = 20,
        aoe_first_attempt_minutes: int | None = None,
        aoe_second_attempt_minutes: int | None = None,
        aoe_joint_minutes: int | None = None,
        aoe_auto_submit_flag: bool = True,
        knowledge_source: KnowledgeSource = "online",
        produce_report: bool = True,
    ):
        if knowledge_source not in {"online", "local"}:
            raise ValueError("knowledge_source must be online or local")
        if knowledge_source == "local":
            status = await asyncio.to_thread(LocalKnowledge(self.config).status)
            if not status["available"]:
                raise ValueError("本地 CTF Wiki 尚未建立索引，请先运行 tga3 sync-knowledge")
        scene = self.config.scene(scene_id)
        model_overrides = model_overrides or {}
        unknown_agents = sorted(set(model_overrides).difference(self.config.agents.agents))
        if unknown_agents:
            raise ValueError(f"unknown task agent model overrides: {', '.join(unknown_agents)}")
        resolved_agents = {
            agent_id: self.config.resolve_agent(
                agent_id, *(model_overrides.get(agent_id) or (None, None, None)),
            )
            for agent_id, configured in self.config.agents.agents.items()
        }
        stage_minutes = {"first": aoe_first_attempt_minutes, "second": aoe_second_attempt_minutes,
                         "joint": aoe_joint_minutes}
        if scene_id == "aoe" and any(not 1 <= value <= 1440
                                    for value in [aoe_attempt_minutes, *stage_minutes.values()] if value is not None):
            raise ValueError("AOE attempt budgets must be between 1 and 1440 minutes")
        budget_metadata = ({"aoe_attempt_budgets": {
            stage: (minutes if minutes is not None else aoe_attempt_minutes) * 60
            for stage, minutes in stage_minutes.items()
        }} if any(value is not None for value in stage_minutes.values()) else
            {"aoe_attempt_seconds": aoe_attempt_minutes * 60})
        metadata = {"knowledge_source": knowledge_source, "produce_report": produce_report}
        if scene_id == "aoe":
            metadata.update({**budget_metadata, "aoe_phase": "collecting",
                             "aoe_auto_submit_flag": aoe_auto_submit_flag,
                             "aoe_scout_mode": "collection_only", "scout_complete": False})
        task = await self.storage.create_task(title, scene.id, task_id, metadata=metadata)
        self.config.ensure_task_directories(task.id)
        await self.storage.update_task(task.id, state=RunState.STARTING)
        for agent_id, configured in self.config.agents.agents.items():
            resolved = resolved_agents.get(agent_id)
            state = AgentState.CREATED
            await self.storage.upsert_agent(
                AgentRun(
                    task_id=task.id,
                    agent_id=agent_id,
                    sdk=resolved.runtime if resolved else configured.runtime,
                    desired_state=state,
                    actual_state=state,
                    provider_id=resolved.provider.id if resolved else configured.provider_id,
                    model_id=resolved.model.id if resolved else configured.model_id,
                    protocol=resolved.protocol if resolved else configured.protocol,
                )
            )
        await self.blackboard.publish(
            task.id,
            SYSTEM_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                topic="scene",
                body={"text": f"场景：{scene.name}\n\n{scene.system_prompt}"},
                idempotency_key="scene-prompt",
            ),
        )
        await self.blackboard.publish(
            task.id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                topic="task",
                body={"text": initial_prompt},
                idempotency_key="initial-user-prompt",
            ),
        )
        for name, media_type, content in initial_files or []:
            await self.add_input_file(task.id, name, media_type, content)
        task = await self.storage.update_task(task.id, state=RunState.SCOUTING)
        self._startup_jobs[task.id] = asyncio.create_task(
            self.aoe.run(task.id, resolved_agents) if scene_id == "aoe"
            else self._scout_then_workers(task.id, resolved_agents))
        return task

    async def restore_aoe(self) -> None:
        await self.aoe.restore_paths()
        for parent in await self.storage.list_tasks(limit=10000):
            if parent.scene_id != "aoe" or parent.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                continue
            bindings = {run.agent_id: self.config.resolve_agent(
                            run.agent_id, run.provider_id, run.model_id, run.protocol)
                        for run in await self.storage.list_agents(parent.id)}
            self._startup_jobs[parent.id] = asyncio.create_task(self.aoe.run(parent.id, bindings))

    async def _scout_then_workers(self, task_id: UUID, bindings: dict[str, ResolvedAgent]) -> None:
        scout = await self.storage.update_agent(
            task_id, "scout", desired_state=AgentState.RUNNING, actual_state=AgentState.STARTING
        )
        actor = self.actor_for(scout)
        budgets = {"executing": self.config.runtime.scout_timeout_seconds,
                   "extracting": None, "merging": None}

        async def emit(method: str, params: dict) -> None:
            if method == "container.started":
                await self.storage.update_agent(
                    task_id, "scout", container_id=params["container_id"], actual_state=AgentState.RUNNING
                )
                await self.events.announce_status(task_id, actor, AgentState.RUNNING.value)
            else:
                await self.handle_agent_event(task_id, "scout", method, params)

        try:
            outcomes = {}
            for phase, budget in budgets.items():
                def phase_metadata(metadata, current_phase=phase, current_budget=budget):
                    return {**metadata, "scout_phase": current_phase, "scout_phase_budget_seconds": current_budget}

                await self.storage.mutate_agent_metadata(task_id, "scout", phase_metadata)
                await self.storage.update_agent(task_id, "scout", actual_state=AgentState.STARTING)
                label = {"executing": "第一轮：侦察执行中", "extracting": "第二轮：Intel 提取中",
                         "merging": "第三轮：Intel 合并中"}[phase]
                deadline_text = f"（预算 {budget} 秒）" if budget is not None else "（全部覆盖后结束）"
                await self.events.emit(task_id, actor=actor, kind=EventKind.SYSTEM,
                                       text=f"{label}{deadline_text}", channel_agent_id="scout",
                                       payload={"scout_phase": phase, "budget_seconds": budget})
                reason, error = "completed", None
                try:
                    # The in-container deadline includes CLI process-tree cleanup.
                    async with asyncio.timeout(budget + 10 if budget is not None else None):
                        await self.containers.scout(
                            LaunchSpec(task_id=task_id, agent=bindings["scout"], scout_phase=phase,
                                       phase_budget_seconds=budget), emit,
                        )
                except TimeoutError:
                    reason = "timeout"
                except Exception as exc:
                    reason, error = "error", str(exc)
                outcomes[phase] = {"finish_reason": reason, "error": error, "budget_seconds": budget}
                await self.storage.update_agent(
                    task_id, "scout", metadata={f"scout_{phase}_result": outcomes[phase]},
                )
                if phase != "executing":
                    if reason != "completed":
                        raise RuntimeError(f"Scout {phase} failed: {error or reason}")
                    coverage = (self.scout_intel.validate_extraction(task_id) if phase == "extracting"
                                else self.scout_intel.validate_merge(task_id))
                    await self.storage.update_agent(task_id, "scout", metadata=coverage)
            await self.storage.update_agent(task_id, "scout", metadata={"scout_phase": "importing"})
            intel_count = await self.scout_intel.flush(task_id)
            result = outcomes["merging"]
            state = (AgentState.FAILED if result["finish_reason"] == "error" else
                     AgentState.STOPPED if result["finish_reason"] == "timeout" else AgentState.COMPLETED)
            await self.storage.update_agent(
                task_id, "scout", desired_state=state, actual_state=state, last_error=result["error"],
                metadata={"scout_phase": "completed", "finish_reason": result["finish_reason"],
                          "intel_count": intel_count},
            )
            await self.events.announce_status(
                task_id, actor, state.value, f"系统已从 /intels/merged 导入 {intel_count} 条 Intel"
            )
            await self.events.emit(
                task_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                text=(f"Scout 三轮结束，系统已导入 {intel_count} 条合并 Intel，"
                      f"开始启动 {len(await self._worker_runs(task_id))} 位 Worker。"),
                payload={"intel_count": intel_count, "scout_phases": outcomes},
            )
            task = await self.storage.update_task(task_id, state=RunState.RUNNING)
            await self._launch_workers(
                task_id, bindings, knowledge_source=task.metadata.get("knowledge_source", "online")
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.storage.update_task(task_id, state=RunState.FAILED)
            current = await self.storage.get_agent(task_id, "scout")
            if current.actual_state not in {AgentState.COMPLETED, AgentState.STOPPED}:
                await self.storage.update_agent(
                    task_id, "scout", desired_state=AgentState.FAILED, actual_state=AgentState.FAILED,
                    last_error=str(exc),
                )
            await self.events.emit(
                task_id, actor=SYSTEM_ACTOR, kind=EventKind.ERROR, text=f"任务启动失败：{exc}",
            )
            await asyncio.gather(
                *(self.containers.stop(task_id, agent_id) for agent_id in bindings), return_exceptions=True,
            )

    async def _launch_workers(
        self,
        task_id: UUID,
        bindings: dict[str, ResolvedAgent],
        *,
        knowledge_source: KnowledgeSource = "online",
        fresh_session: bool = False,
    ) -> None:
        board_reset = {"blackboard_cursor": 0, "blackboard_pending": None, "blackboard_read_at": 0}
        for agent_id in self.config.worker_agent_ids:
            if agent_id not in bindings:
                continue
            run = await self.storage.update_agent(
                task_id,
                agent_id,
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.STARTING,
                container_id=None,
                last_error=None,
                **({"metadata": board_reset} if fresh_session else {}),
            )
            launch = asyncio.create_task(
                self.containers.launch(
                    LaunchSpec(
                        task_id=task_id,
                        agent=bindings[agent_id],
                        knowledge_source=knowledge_source,
                        fresh_session=fresh_session,
                    )
                )
            )
            try:
                container_id = await asyncio.shield(launch)
            except asyncio.CancelledError:
                await launch
                raise
            await self.storage.update_agent(task_id, agent_id, container_id=container_id)
            await self.events.announce_status(task_id, self.actor_for(run), AgentState.STARTING.value)

    async def restart_task(
        self,
        task_id: UUID,
        *,
        rerun_scout: bool = False,
        retry_abandoned: bool = False,
        model_overrides: dict[str, tuple[str, str, ProviderProtocol]] | None = None,
        aoe_first_attempt_minutes: int | None = None,
        aoe_second_attempt_minutes: int | None = None,
        aoe_joint_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Restart an ended task in-place from its highest completed checkpoint."""
        task = await self.storage.get_task(task_id)
        if task.parent_task_id is not None:
            raise ContractError("请对主任务执行重新启动")
        if task.state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            raise ConflictError("任务仍在运行中，请先停止任务再重新启动")
        existing_job = self._startup_jobs.get(task_id)
        if existing_job is not None and not existing_job.done():
            raise ConflictError("任务调度器仍在收尾退出，请稍后再试")

        model_overrides = model_overrides or {}
        unknown_agents = sorted(set(model_overrides).difference(self.config.agents.agents))
        if unknown_agents:
            raise ValueError(f"unknown task agent model overrides: {', '.join(unknown_agents)}")

        stage_minutes = {
            "first": aoe_first_attempt_minutes,
            "second": aoe_second_attempt_minutes,
            "joint": aoe_joint_minutes,
        }
        if task.scene_id == "aoe" and any(
            not 1 <= value <= 1440 for value in stage_minutes.values() if value is not None
        ):
            raise ValueError("AOE attempt budgets must be between 1 and 1440 minutes")

        existing_runs = {run.agent_id: run for run in await self.storage.list_agents(task_id)}
        resolved_agents: dict[str, ResolvedAgent] = {}
        fallback_models: list[str] = []
        for agent_id, configured in self.config.agents.agents.items():
            if agent_id in model_overrides:
                resolved = self.config.resolve_agent(agent_id, *model_overrides[agent_id])
            elif agent_id in existing_runs:
                run = existing_runs[agent_id]
                try:
                    resolved = self.config.resolve_agent(agent_id, run.provider_id, run.model_id, run.protocol)
                except (KeyError, ValueError):
                    resolved = self.config.resolve_agent(agent_id)
                    fallback_models.append(configured.display_name)
            else:
                resolved = self.config.resolve_agent(agent_id)
            resolved_agents[agent_id] = resolved

        restart_count = int(task.metadata.get("restart_count", 0) or 0) + 1
        for agent_id, resolved in resolved_agents.items():
            if agent_id in existing_runs:
                await self.storage.update_agent(
                    task_id,
                    agent_id,
                    sdk=resolved.runtime,
                    provider_id=resolved.provider.id,
                    model_id=resolved.model.id,
                    protocol=resolved.protocol,
                    container_id=None,
                    last_error=None,
                    **({"metadata": {"aoe_paused": False}} if task.scene_id == "aoe" else {}),
                )
            else:
                await self.storage.upsert_agent(
                    AgentRun(
                        task_id=task_id,
                        agent_id=agent_id,
                        sdk=resolved.runtime,
                        provider_id=resolved.provider.id,
                        model_id=resolved.model.id,
                        protocol=resolved.protocol,
                    )
                )

        if task.scene_id == "aoe":
            new_budgets = None
            if any(value is not None for value in stage_minutes.values()):
                current_budgets = attempt_budgets(task.metadata)
                new_budgets = {
                    stage: (minutes * 60 if minutes is not None else current_budgets[stage])
                    for stage, minutes in stage_minutes.items()
                }
            summary = await self.aoe.prepare_restart(
                task_id,
                rerun_scout=rerun_scout,
                retry_abandoned=retry_abandoned,
                budgets=new_budgets,
                restart_count=restart_count,
            )
            detail_parts = []
            if summary["need_scout"]:
                detail_parts.append("重跑 Scout 收集")
            else:
                detail_parts.append("跳过 Scout")
            if summary["resumed_questions"]:
                detail_parts.append(f"续跑 {summary['resumed_questions']} 道未完成题")
            if summary["requeued_abandoned"]:
                detail_parts.append(f"重新挑战 {summary['requeued_abandoned']} 道已放弃题")
            if summary["pending_reports"]:
                detail_parts.append(f"补跑 {summary['pending_reports']} 份报告")
            await self.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：{'，'.join(detail_parts)}。",
                payload={"restart_count": restart_count, **summary, "fallback_models": fallback_models},
            )
            self._startup_jobs[task_id] = asyncio.create_task(self.aoe.run(task_id, resolved_agents))
            refreshed = await self.storage.get_task(task_id)
            return {
                "task": refreshed.model_dump(mode="json"),
                "resumed_from": summary["resumed_from"],
                "restart_count": restart_count,
                "fallback_models": fallback_models,
                "summary": summary,
            }

        # Normal single-question task restart.
        scout_run = existing_runs.get("scout")
        scout_done = bool(scout_run and scout_run.metadata.get("scout_phase") == "completed")
        entries = await self.storage.list_entries(task_id, limit=10_000)
        has_final = any(entry.kind == EntryKind.FINAL_CANDIDATE for entry in entries)

        if has_final and task.state == RunState.FAILED and not rerun_scout and self.aoe.automation is not None:
            resumed_from = "reporter"
            await self.storage.update_agent(
                task_id,
                "reporter",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.CREATED,
                container_id=None,
                last_error=None,
            )
            refreshed = await self.storage.update_task(
                task_id, state=RunState.REPORTING, metadata={"restart_count": restart_count}
            )
            await self.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：已有最终候选，直接重跑 Reporter 生成报告。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )
            self.aoe.automation.start_report(task_id)
        elif scout_done and not rerun_scout:
            resumed_from = "workers"
            refreshed = await self.storage.update_task(
                task_id, state=RunState.RUNNING, metadata={"restart_count": restart_count}
            )
            await self.blackboard.publish(
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
            await self.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：保留已有 Intel 与黑板，直接启动 Workers 续跑。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )

            async def launch_only():
                try:
                    await self._launch_workers(
                        task_id,
                        resolved_agents,
                        knowledge_source=refreshed.metadata.get("knowledge_source", "online"),
                        fresh_session=True,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self.storage.update_task(task_id, state=RunState.FAILED)
                    await self.events.emit(
                        task_id, actor=SYSTEM_ACTOR, kind=EventKind.ERROR, text=f"任务重启失败：{exc}"
                    )

            self._startup_jobs[task_id] = asyncio.create_task(launch_only())
        else:
            resumed_from = "scout"
            await self.storage.update_agent(
                task_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.CREATED,
                container_id=None,
                last_error=None,
                metadata={"scout_phase": "executing"},
            )
            refreshed = await self.storage.update_task(
                task_id, state=RunState.SCOUTING, metadata={"restart_count": restart_count}
            )
            if entries:
                await self.blackboard.publish(
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
            await self.events.emit(
                task_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"任务已原地重启（第 {restart_count} 次运行）：从 Scout 阶段重新开始。",
                payload={"restart_count": restart_count, "resumed_from": resumed_from},
            )
            self._startup_jobs[task_id] = asyncio.create_task(self._scout_then_workers(task_id, resolved_agents))

        return {
            "task": refreshed.model_dump(mode="json"),
            "resumed_from": resumed_from,
            "restart_count": restart_count,
            "fallback_models": fallback_models,
        }

    async def clone_config(self, task_id: UUID) -> dict[str, Any]:
        """Extract reusable configuration from an existing task to prefill a clean new task."""
        task = await self.storage.get_task(task_id)
        if task.parent_task_id is not None:
            task = await self.storage.get_task(task.parent_task_id)
        entries = await self.storage.list_entries(task.id, limit=500)
        prompt_entry = next(
            (e for e in entries if e.idempotency_key == "initial-user-prompt"),
            next((e for e in entries if e.kind == EntryKind.USER_PROMPT and e.topic == "task"), None),
        )
        prompt_text = str((prompt_entry.body or {}).get("text", "")) if prompt_entry else ""
        agents = await self.storage.list_agents(task.id)
        files = await self.storage.list_input_files(task.id)
        budgets_sec = attempt_budgets(task.metadata)
        return {
            "source_task_id": str(task.id),
            "title": task.title,
            "scene_id": task.scene_id,
            "prompt": prompt_text,
            "knowledge_source": task.metadata.get("knowledge_source", "online"),
            "produce_report": bool(task.metadata.get("produce_report", True)),
            "aoe_auto_submit_flag": bool(task.metadata.get("aoe_auto_submit_flag", True)),
            "aoe_budgets": {
                "first": max(1, round(budgets_sec["first"] / 60)),
                "second": max(1, round(budgets_sec["second"] / 60)),
                "joint": max(1, round(budgets_sec["joint"] / 60)),
            },
            "agent_models": {
                run.agent_id: {
                    "provider_id": run.provider_id,
                    "model_id": run.model_id,
                    "protocol": run.protocol,
                }
                for run in agents
            },
            "input_files": [
                {"id": str(item.id), "name": item.name, "size_bytes": item.size_bytes}
                for item in files
            ],
        }

    async def cancel_startup(self, task_id: UUID) -> None:
        job = self._startup_jobs.pop(task_id, None)
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            await asyncio.gather(
                *(self.containers.stop(task_id, agent_id) for agent_id in self.config.agents.agents),
                return_exceptions=True,
            )
            task = await self.storage.get_task(task_id)
            if task.scene_id == "aoe" and task.parent_task_id is None:
                # Shutdown preserves current attempt deadlines/history for same-question recovery.
                for question in await self.storage.list_questions(task_id):
                    from .aoe_models import active_attempts

                    await asyncio.gather(*(self.aoe._stop_worker(question.id, a["agent_id"])
                                           for a in active_attempts(question)))
                    if self.aoe.automation is not None:
                        await self.aoe.automation.cancel(question.id)
                    await self.containers.stop(question.id, "reporter")

    async def close(self) -> None:
        for task_id in list(self._startup_jobs):
            await self.cancel_startup(task_id)
        await self.aoe.close()

    async def add_input_file(
        self, task_id: UUID, raw_name: str, media_type: str, content: bytes
    ) -> tuple[InputFile, UUID]:
        await self.storage.get_task(task_id)
        name = Path(raw_name or "upload.bin").name
        item_id = uuid4()
        root = self.config.task_path(task_id, "inputs")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{item_id}-{name}"
        path.write_bytes(content)
        item = InputFile(
            id=item_id,
            task_id=task_id,
            name=name,
            storage_path=str(path),
            media_type=media_type or "application/octet-stream",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        item = await self.storage.register_input_file(item)
        body = UserFileBody(
            input_file_id=item.id,
            name=item.name,
            media_type=item.media_type,
            sha256=item.sha256,
        )
        entry = await self.blackboard.publish(
            task_id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_FILE,
                topic="input",
                body=body.model_dump(mode="json"),
                idempotency_key=f"file-{item.id}",
            ),
        )
        return item, entry.id

    async def blackboard_changed(self, task_id: UUID, latest_seq: int) -> None:
        await self.gateway.broadcast(task_id, "blackboard.changed", {"latest_seq": latest_seq})
        await asyncio.gather(
            *(handler(task_id, latest_seq) for handler in self.change_handlers),
            return_exceptions=True,
        )

    async def handle_agent_event(self, task_id: UUID, agent_id: str, method: str, params: dict[str, Any]) -> None:
        run = await self.storage.get_agent(task_id, agent_id)
        actor = self.actor_for(run)
        if method == "session.hello":
            session_id = str(params.get("session_id", "")) or None
            run = await self.storage.update_agent(
                task_id, agent_id, session_id=session_id, actual_state=AgentState.RUNNING
            )
            await self.events.announce_status(task_id, self.actor_for(run), AgentState.RUNNING.value)
            task = await self.storage.get_task(task_id)
            if task.parent_task_id:
                from .aoe_models import active_attempts
                active = next((a for a in active_attempts(task) if a['agent_id'] == agent_id), None)
                if active and active.get('deadline') is not None:
                    await self.gateway.notify(task_id, agent_id, 'session.deadline',
                        {'attempt_id': active['id'], 'deadline': active['deadline']})
        elif method == "agent.status":
            state = AgentState(str(params["state"]))
            if run.metadata.get('aoe_attempt_id') and state in {AgentState.PAUSED, AgentState.RUNNING}:
                await self.aoe.record_pause(task_id, agent_id, paused=state == AgentState.PAUSED)
            changes = {"actual_state": state}
            if state in {AgentState.RUNNING, AgentState.IDLE}:
                changes["last_error"] = None
            run = await self.storage.update_agent(task_id, agent_id, **changes)
            await self.events.announce_status(
                task_id, self.actor_for(run), state.value, str(params.get("detail", ""))
            )
        elif method == "agent.session":
            await self.storage.update_agent(task_id, agent_id, metadata=params)
        elif method == "agent.output.delta":
            await self.events.emit(
                task_id,
                actor=actor,
                kind=EventKind.ASSISTANT_DELTA,
                text=str(params.get("text", " ")),
                channel_agent_id=agent_id,
            )
        elif method in {"agent.action.started", "agent.action.completed"}:
            if method == 'agent.action.started' and params.get('tool_use_id') and run.metadata.get('aoe_attempt_id'):
                # Native built-in web search has no PreToolUse hook.
                await self.aoe.action_started(task_id, agent_id, run.metadata['aoe_attempt_id'])
            kind = EventKind.ACTION_STARTED if method.endswith("started") else EventKind.ACTION_COMPLETED
            await self.events.emit(
                task_id,
                actor=actor,
                kind=kind,
                text=str(params.get("summary", method)),
                channel_agent_id=agent_id,
                payload=params,
            )
        elif method == "agent.recovery":
            await self.events.emit(
                task_id, actor=actor, kind=EventKind.SYSTEM,
                text=str(params.get("summary", "CLI 会话恢复")), channel_agent_id=agent_id, payload=params,
            )
        elif method == "agent.error":
            detail = str(params.get("message", "agent error"))
            await self.storage.update_agent(task_id, agent_id, actual_state=AgentState.FAILED, last_error=detail)
            await self.events.emit(
                task_id, actor=actor, kind=EventKind.ERROR, text=detail, channel_agent_id=agent_id
            )
        elif method == "session.disconnected":
            state = (
                AgentState.STOPPED
                if run.desired_state in {AgentState.STOPPING, AgentState.STOPPED}
                else AgentState.FAILED
            )
            detail = "控制通道已关闭" if state == AgentState.STOPPED else "控制通道意外断开"
            run = await self.storage.update_agent(
                task_id,
                agent_id,
                actual_state=state,
                last_error=None if state == AgentState.STOPPED else detail,
            )
            await self.events.announce_status(task_id, self.actor_for(run), state.value, detail)



    async def pause_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        task = await self.storage.get_task(task_id)
        if task.scene_id == "aoe" and task.parent_task_id is None:
            slot = await self._worker_run(task_id, agent_id)
            await self.storage.update_agent(task_id, agent_id, metadata={"aoe_paused": True})
            if slot.metadata.get("current_question_id"):
                await self.pause_agent(UUID(slot.metadata["current_question_id"]), agent_id)
            return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSED,
                                                     actual_state=AgentState.PAUSED)
        await self._worker_run(task_id, agent_id)
        await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSE_REQUESTED)
        await self.gateway.request(task_id, agent_id, "session.pause")
        return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSED)

    async def resume_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        task = await self.storage.get_task(task_id)
        if task.scene_id == "aoe" and task.parent_task_id is None:
            slot = await self._worker_run(task_id, agent_id)
            await self.storage.update_agent(task_id, agent_id, metadata={"aoe_paused": False})
            if slot.metadata.get("current_question_id"):
                await self.resume_agent(UUID(slot.metadata["current_question_id"]), agent_id)
            return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.RUNNING,
                                                     actual_state=AgentState.IDLE)
        await self._worker_run(task_id, agent_id)
        await self.gateway.request(task_id, agent_id, "session.resume")
        return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.RUNNING)

    async def set_agent_model(
        self, task_id: UUID, agent_id: str, provider_id: str, model_id: str, protocol: ProviderProtocol
    ) -> AgentRun:
        task = await self.storage.get_task(task_id)
        if task.scene_id == "aoe" and task.parent_task_id is None:
            slot = await self._worker_run(task_id, agent_id)
            self.config.resolve_agent(agent_id, provider_id, model_id, protocol)
            if slot.metadata.get("current_question_id"):
                await self.set_agent_model(UUID(slot.metadata["current_question_id"]), agent_id,
                                           provider_id, model_id, protocol)
            return await self.storage.update_agent(task_id, agent_id, provider_id=provider_id,
                                                     model_id=model_id, protocol=protocol)
        current = await self._worker_run(task_id, agent_id)
        provider = self.config.models.provider(provider_id)
        self.config.validate_runtime_protocol(current.sdk, protocol)
        model = provider.model(model_id, protocol, require_enabled=True)
        await self.gateway.request(
            task_id,
            agent_id,
            "session.set_model",
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "model_name": model.name,
                "protocol": protocol,
                "base_url": provider.sdk_base_url(protocol),
                "api_key": provider.key(),
            },
        )
        run = await self.storage.update_agent(
            task_id, agent_id, provider_id=provider_id, model_id=model_id, protocol=protocol
        )
        await self.events.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.MODEL_CHANGED,
            text=f"{agent_id} 已切换到 {provider_id}/{model_id}（{protocol}）",
            channel_agent_id=agent_id,
        )
        return run

    async def _worker_run(self, task_id: UUID, agent_id: str) -> AgentRun:
        run = await self.storage.get_agent(task_id, agent_id)
        configured = self.config.agents.agents.get(agent_id)
        if configured is None or configured.role != "worker":
            raise ValueError(f"runtime control is available only for worker agents: {agent_id}")
        return run

    async def _worker_runs(self, task_id: UUID) -> list[AgentRun]:
        return [
            run
            for run in await self.storage.list_agents(task_id)
            if run.agent_id in self.config.agents.agents and self.config.agents.agents[run.agent_id].role == "worker"
        ]

    async def finish_task_workers(self, task_id: UUID) -> None:
        workers = await self._worker_runs(task_id)
        for run in workers:
            await self.storage.update_agent(
                task_id,
                run.agent_id,
                desired_state=AgentState.STOPPING,
            )
            await self.gateway.notify(task_id, run.agent_id, "session.stop")
        await asyncio.gather(*(self.containers.stop(task_id, run.agent_id) for run in workers), return_exceptions=True)
        for run in workers:
            await self.storage.update_agent(
                task_id,
                run.agent_id,
                desired_state=AgentState.STOPPED,
                actual_state=AgentState.STOPPED,
                last_error=None,
            )

    async def stop_task(self, task_id: UUID) -> None:
        task = await self.storage.get_task(task_id)
        await self.cancel_startup(task_id)
        if task.scene_id == "aoe" and task.parent_task_id is None:
            await self.aoe.stop_children(task_id, cancelled=True)
            await self.storage.update_task(task_id, metadata={"aoe_phase": "cancelled"})
        await self.containers.stop(task_id, "scout")
        # Cancellation retains raw/merged JSON without publishing an incomplete handoff.
        workers = await self._worker_runs(task_id)
        for run in workers:
            await self.storage.update_agent(task_id, run.agent_id, desired_state=AgentState.STOPPING)
            await self.gateway.notify(task_id, run.agent_id, "session.stop")
        await asyncio.gather(*(self.containers.stop(task_id, run.agent_id) for run in workers), return_exceptions=True)
        for run in await self.storage.list_agents(task_id):
            await self.storage.update_agent(
                task_id,
                run.agent_id,
                desired_state=AgentState.STOPPED,
                actual_state=AgentState.STOPPED,
            )
        await self.storage.update_task(task_id, state=RunState.CANCELLED)

    async def delete_task(self, task_id: UUID) -> None:
        await self.cancel_startup(task_id)
        task = await self.storage.get_task(task_id)
        terminal = task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
        if not terminal:
            await self.stop_task(task_id)
        else:
            workers = await self._worker_runs(task_id)
            await asyncio.gather(
                *(self.containers.stop(task_id, run.agent_id) for run in workers),
                return_exceptions=True,
            )
        if task.scene_id == "aoe" and task.parent_task_id is None:
            if not await self.aoe.targets.release_parent(task_id):
                from .errors import ConflictError

                raise ConflictError("Target cleanup is pending; retry deletion after platform close succeeds")
        await self.gateway.disconnect_task(task_id)
        # Keep task data recoverable if the database deletion fails. Filesystem
        # cleanup is intentionally best-effort and only begins after the database
        # transaction has committed.
        await self.storage.delete_task(task_id)
        self.aoe.targets.forget_parent(task_id)
        path = self.config.task_path(task_id)
        await asyncio.to_thread(shutil.rmtree, path, True)


__all__ = ["TaskCoordinator"]
