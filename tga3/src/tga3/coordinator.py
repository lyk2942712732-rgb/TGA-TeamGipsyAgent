"""Thin task lifecycle coordinator; planning remains inside the agents."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
from .events import TaskEvents
from .gateway import AgentGateway
from .provider_profiles import ProviderProtocol
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
        self.change_handlers: list[Callable[[UUID, int], Awaitable[None]]] = []

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
    ):
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
        task = await self.storage.create_task(title, scene.id, task_id)
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
        self._startup_jobs[task.id] = asyncio.create_task(self._scout_then_workers(task.id, resolved_agents))
        return task

    async def _scout_then_workers(self, task_id: UUID, bindings: dict[str, ResolvedAgent]) -> None:
        scout = await self.storage.update_agent(
            task_id, "scout", desired_state=AgentState.RUNNING, actual_state=AgentState.STARTING
        )
        actor = self.actor_for(scout)

        async def emit(method: str, params: dict) -> None:
            if method == "container.started":
                await self.storage.update_agent(
                    task_id, "scout", container_id=params["container_id"], actual_state=AgentState.RUNNING
                )
                await self.events.announce_status(task_id, actor, AgentState.RUNNING.value)
            else:
                await self.handle_agent_event(task_id, "scout", method, params)

        try:
            await self.events.announce_status(task_id, actor, AgentState.STARTING.value)
            timed_out = False
            try:
                async with asyncio.timeout(self.config.runtime.scout_timeout_seconds):
                    await self.containers.scout(LaunchSpec(task_id=task_id, agent=bindings["scout"]), emit)
            except TimeoutError:
                timed_out = True
            # scout() only returns after the container and its tool processes have stopped.
            entries = (await self.blackboard.sync(task_id, limit=10_000)).entries
            intel_count = sum(entry.kind == EntryKind.INTEL for entry in entries)
            state = AgentState.STOPPED if timed_out else AgentState.COMPLETED
            current = await self.storage.get_agent(task_id, "scout")
            await self.storage.update_agent(
                task_id, "scout", desired_state=state, actual_state=state,
                metadata={**current.metadata, "finish_reason": "timeout" if timed_out else "completed",
                          "intel_count": intel_count},
            )
            await self.events.announce_status(
                task_id, actor, state.value, "侦察预算已用尽，保留已有成果" if timed_out else "侦察结束"
            )
            await self.events.emit(
                task_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                text=f"Scout 已交接 {intel_count} 条 Intel，开始启动两个 Worker。"
                     + ("未获得 Intel，Worker 将从题面与原始材料开始。" if not intel_count else ""),
                payload={"intel_count": intel_count, "timed_out": timed_out},
            )
            # Open the Worker write phase before launching either container.
            await self.storage.update_task(task_id, state=RunState.RUNNING)
            for agent_id in self.config.worker_agent_ids:
                run = await self.storage.update_agent(
                    task_id, agent_id, desired_state=AgentState.RUNNING, actual_state=AgentState.STARTING
                )
                launch = asyncio.create_task(
                    self.containers.launch(LaunchSpec(task_id=task_id, agent=bindings[agent_id]))
                )
                try:
                    container_id = await asyncio.shield(launch)
                except asyncio.CancelledError:
                    await launch
                    raise
                await self.storage.update_agent(task_id, agent_id, container_id=container_id)
                await self.events.announce_status(task_id, self.actor_for(run), AgentState.STARTING.value)
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

    async def cancel_startup(self, task_id: UUID) -> None:
        job = self._startup_jobs.pop(task_id, None)
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            await asyncio.gather(
                *(self.containers.stop(task_id, agent_id) for agent_id in self.config.agents.agents),
                return_exceptions=True,
            )

    async def close(self) -> None:
        for task_id in list(self._startup_jobs):
            await self.cancel_startup(task_id)

    async def add_input_file(
        self, task_id: UUID, raw_name: str, media_type: str, content: bytes
    ) -> tuple[InputFile, UUID]:
        await self.storage.get_task(task_id)
        name = Path(raw_name or "upload.bin").name
        item_id = uuid4()
        root = self.config.resolve_path(self.config.runtime.input_root) / str(task_id)
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
        await self.storage.register_input_file(item)
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
        elif method == "agent.status":
            state = AgentState(str(params["state"]))
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
        await self._worker_run(task_id, agent_id)
        await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSE_REQUESTED)
        await self.gateway.request(task_id, agent_id, "session.pause")
        return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSED)

    async def resume_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        await self._worker_run(task_id, agent_id)
        await self.gateway.request(task_id, agent_id, "session.resume")
        return await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.RUNNING)

    async def set_agent_model(
        self, task_id: UUID, agent_id: str, provider_id: str, model_id: str, protocol: ProviderProtocol
    ) -> AgentRun:
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
        await self.cancel_startup(task_id)
        await self.containers.stop(task_id, "scout")
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
        await self.gateway.disconnect_task(task_id)
        # Keep task data recoverable if the database deletion fails. Filesystem
        # cleanup is intentionally best-effort and only begins after the database
        # transaction has committed.
        await self.storage.delete_task(task_id)
        for raw_root in (
            self.config.runtime.workspace_root,
            self.config.runtime.input_root,
            self.config.runtime.artifact_root,
            self.config.runtime.writeup_root,
        ):
            path = self.config.resolve_path(raw_root) / str(task_id)
            await asyncio.to_thread(shutil.rmtree, path, True)


__all__ = ["TaskCoordinator"]
