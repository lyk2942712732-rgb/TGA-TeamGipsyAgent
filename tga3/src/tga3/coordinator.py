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
from .config import TGA3Config
from .dialogue import SolverDialogue
from .docker_runtime import ContainerRuntime, LaunchSpec
from .domain import (
    SYSTEM_ACTOR,
    USER_ACTOR,
    Actor,
    AgentRun,
    AgentState,
    DialogueKind,
    EntryKind,
    InputFile,
    PublishRequest,
    RunState,
    UserFileBody,
)
from .gateway import AgentGateway
from .provider_profiles import ProviderProtocol
from .storage import Storage


class TaskCoordinator:
    def __init__(self, config: TGA3Config, storage: Storage, containers: ContainerRuntime) -> None:
        self.config = config
        self.storage = storage
        self.containers = containers
        self.gateway = AgentGateway(self.handle_agent_event)
        self.dialogue = SolverDialogue(storage)
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
            agent_id: self.config.resolve_agent(agent_id, *(model_overrides.get(agent_id) or (None, None, None)))
            for agent_id in self.config.agents.agents
        }
        task = await self.storage.create_task(title, scene.id, task_id)
        await self.storage.update_task(task.id, state=RunState.STARTING)
        for agent_id, configured in self.config.agents.agents.items():
            resolved = resolved_agents[agent_id]
            state = AgentState.IDLE if configured.role == "supervisor" else AgentState.CREATED
            await self.storage.upsert_agent(
                AgentRun(
                    task_id=task.id,
                    agent_id=agent_id,
                    sdk=resolved.runtime,
                    desired_state=state,
                    actual_state=state,
                    provider_id=resolved.provider.id,
                    model_id=resolved.model.id,
                    protocol=resolved.protocol,
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
        try:
            for agent_id in self.config.worker_agent_ids:
                resolved = resolved_agents[agent_id]
                run = await self.storage.update_agent(
                    task.id,
                    agent_id,
                    desired_state=AgentState.RUNNING,
                    actual_state=AgentState.STARTING,
                )
                container_id = await self.containers.launch(LaunchSpec(task_id=task.id, agent=resolved))
                run = await self.storage.update_agent(task.id, agent_id, container_id=container_id)
                await self.dialogue.announce_status(task.id, self.actor_for(run), AgentState.STARTING.value)
            return await self.storage.update_task(task.id, state=RunState.RUNNING)
        except Exception:
            await self.storage.update_task(task.id, state=RunState.FAILED)
            await asyncio.gather(
                *(self.containers.stop(task.id, agent_id) for agent_id in self.config.worker_agent_ids),
                return_exceptions=True,
            )
            raise

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
            await self.dialogue.announce_status(task_id, self.actor_for(run), AgentState.RUNNING.value)
        elif method == "agent.status":
            state = AgentState(str(params["state"]))
            changes = {"actual_state": state}
            if state in {AgentState.RUNNING, AgentState.IDLE}:
                changes["last_error"] = None
            run = await self.storage.update_agent(task_id, agent_id, **changes)
            await self.dialogue.announce_status(
                task_id, self.actor_for(run), state.value, str(params.get("detail", ""))
            )
        elif method == "agent.output.delta":
            await self.dialogue.emit(
                task_id,
                actor=actor,
                kind=DialogueKind.ASSISTANT_DELTA,
                text=str(params.get("text", " ")),
                channel_agent_id=agent_id,
            )
        elif method in {"agent.action.started", "agent.action.completed"}:
            kind = DialogueKind.ACTION_STARTED if method.endswith("started") else DialogueKind.ACTION_COMPLETED
            await self.dialogue.emit(
                task_id,
                actor=actor,
                kind=kind,
                text=str(params.get("summary", method)),
                channel_agent_id=agent_id,
                payload=params,
            )
        elif method == "agent.needs_user_input":
            await self.dialogue.ask(
                task_id,
                supervisor=self.supervisor_actor(),
                origin=actor,
                question=str(params["question"]),
            )
            await self.storage.update_task(task_id, state=RunState.WAITING_USER)
        elif method == "agent.error":
            detail = str(params.get("message", "agent error"))
            await self.storage.update_agent(task_id, agent_id, actual_state=AgentState.FAILED, last_error=detail)
            await self.dialogue.emit(
                task_id, actor=actor, kind=DialogueKind.ERROR, text=detail, channel_agent_id=agent_id
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
            await self.dialogue.announce_status(task_id, self.actor_for(run), state.value, detail)

    def supervisor_actor(self) -> Actor:
        binding = self.config.agents.agents["supervisor"]
        return Actor(
            agent_id="supervisor",
            display_name=binding.display_name,
            role="supervisor",
            sdk=binding.runtime,
            model=binding.model_id,
        )

    async def add_prompt(
        self,
        task_id: UUID,
        text: str,
        *,
        addressed_to: list[str] | None = None,
        attachment_ids: list[UUID] | None = None,
        idempotency_key: str,
    ):
        entry = await self.blackboard.publish(
            task_id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                topic="follow-up",
                body={"text": text, "addressed_to": addressed_to or [], "attachment_ids": attachment_ids or []},
                idempotency_key=idempotency_key,
            ),
        )
        await self.dialogue.emit(
            task_id,
            actor=USER_ACTOR,
            kind=DialogueKind.USER_MESSAGE,
            text=text,
            channel_agent_id=(addressed_to or ["supervisor"])[0],
            payload={"addressed_to": addressed_to or []},
        )
        return entry

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
        provider.model(model_id)
        if protocol not in provider.protocols:
            raise ValueError(f"provider {provider.id} does not support protocol={protocol}")
        self.config.validate_runtime_protocol(current.sdk, protocol)
        await self.gateway.request(
            task_id,
            agent_id,
            "session.set_model",
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "model_name": provider.model(model_id).name,
                "protocol": protocol,
                "base_url": provider.sdk_base_url(protocol),
                "api_key": provider.key(),
            },
        )
        run = await self.storage.update_agent(
            task_id, agent_id, provider_id=provider_id, model_id=model_id, protocol=protocol
        )
        await self.dialogue.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.MODEL_CHANGED,
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
            if self.config.agents.agents[run.agent_id].role == "worker"
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
