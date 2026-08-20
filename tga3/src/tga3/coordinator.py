"""Thin task lifecycle coordinator; planning remains inside the agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

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
    PublishRequest,
    RunState,
)
from .gateway import AgentGateway
from .storage import Storage


class TaskCoordinator:
    WORKERS = ("worker-openai", "worker-claude")

    def __init__(self, config: TGA3Config, storage: Storage, containers: ContainerRuntime) -> None:
        self.config = config
        self.storage = storage
        self.containers = containers
        self.gateway = AgentGateway(self.handle_agent_event)
        self.dialogue = SolverDialogue(storage)
        self.blackboard = Blackboard(storage, self.blackboard_changed)
        self.change_handlers: list[Callable[[UUID, int], Awaitable[None]]] = []

    @staticmethod
    def actor_for(run: AgentRun) -> Actor:
        return Actor(
            agent_id=run.agent_id,
            display_name=run.agent_id,
            role="worker",
            sdk=run.sdk,
            model=run.model_id,
        )

    async def create_and_start(self, title: str, initial_prompt: str, task_id: UUID | None = None):
        task = await self.storage.create_task(title, task_id)
        await self.storage.update_task(task.id, state=RunState.STARTING)
        await self.blackboard.publish(
            task.id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                topic="task",
                body={"text": initial_prompt},
                idempotency_key="initial-prompt",
            ),
        )
        try:
            for agent_id in self.WORKERS:
                resolved = self.config.resolve_agent(agent_id)
                run = AgentRun(
                    task_id=task.id,
                    agent_id=agent_id,
                    sdk=resolved.runtime,
                    desired_state=AgentState.RUNNING,
                    actual_state=AgentState.STARTING,
                    provider_id=resolved.provider.id,
                    model_id=resolved.model.id,
                )
                await self.storage.upsert_agent(run)
                container_id = await self.containers.launch(LaunchSpec(task_id=task.id, agent=resolved))
                await self.storage.update_agent(task.id, agent_id, container_id=container_id)
                await self.dialogue.announce_status(task.id, self.actor_for(run), AgentState.STARTING.value)
            return await self.storage.update_task(task.id, state=RunState.RUNNING)
        except Exception:
            await self.storage.update_task(task.id, state=RunState.FAILED)
            await asyncio.gather(
                *(self.containers.stop(task.id, agent_id) for agent_id in self.WORKERS),
                return_exceptions=True,
            )
            raise

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
            run = await self.storage.update_agent(task_id, agent_id, actual_state=state)
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
            supervisor = self.supervisor_actor(task_id)
            await self.dialogue.ask(
                task_id,
                supervisor=supervisor,
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
        # heartbeat is deliberately ephemeral; it does not pollute the blackboard/dialogue.

    def supervisor_actor(self, task_id: UUID) -> Actor:
        binding = self.config.agent_models.agents["supervisor"]
        return Actor(
            agent_id="supervisor",
            display_name="Supervisor",
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
            payload={"addressed_to": addressed_to or []},
        )
        return entry

    async def pause_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        run = await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.PAUSED)
        await self.gateway.request(task_id, agent_id, "session.pause")
        return run

    async def resume_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        run = await self.storage.update_agent(task_id, agent_id, desired_state=AgentState.RUNNING)
        await self.gateway.request(task_id, agent_id, "session.resume")
        return run

    async def set_agent_model(self, task_id: UUID, agent_id: str, provider_id: str, model_id: str) -> AgentRun:
        current = await self.storage.get_agent(task_id, agent_id)
        provider = self.config.models.provider(provider_id)
        provider.model(model_id)
        if current.sdk == "claude_agent" and provider.protocol != "anthropic":
            raise ValueError("Claude Agent SDK can only use an anthropic provider")
        if current.sdk == "openai_agents" and provider.protocol == "anthropic":
            raise ValueError("OpenAI Agents SDK cannot use an anthropic provider")
        await self.gateway.request(
            task_id,
            agent_id,
            "session.set_model",
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "model_name": provider.model(model_id).name,
                "protocol": provider.protocol,
                "base_url": provider.base_url,
                "api_key": provider.key(),
            },
        )
        run = await self.storage.update_agent(task_id, agent_id, provider_id=provider_id, model_id=model_id)
        await self.dialogue.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.MODEL_CHANGED,
            text=f"{agent_id} 已切换到 {provider_id}/{model_id}",
            channel_agent_id=agent_id,
        )
        return run

    async def finish_task_workers(self, task_id: UUID) -> None:
        agents = await self.storage.list_agents(task_id)
        for run in agents:
            await self.gateway.notify(task_id, run.agent_id, "session.stop")
        await asyncio.gather(*(self.containers.stop(task_id, run.agent_id) for run in agents), return_exceptions=True)
        for run in agents:
            await self.storage.update_agent(
                task_id,
                run.agent_id,
                desired_state=AgentState.STOPPED,
                actual_state=AgentState.STOPPED,
            )

    async def stop_task(self, task_id: UUID) -> None:
        agents = await self.storage.list_agents(task_id)
        for run in agents:
            await self.storage.update_agent(task_id, run.agent_id, desired_state=AgentState.STOPPING)
            await self.gateway.notify(task_id, run.agent_id, "session.stop")
        await asyncio.gather(*(self.containers.stop(task_id, run.agent_id) for run in agents), return_exceptions=True)
        await self.storage.update_task(task_id, state=RunState.CANCELLED)


__all__ = ["TaskCoordinator"]
