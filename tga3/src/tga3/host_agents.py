"""One-shot CLI report finalization after both Workers stop."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from .blackboard import Blackboard
from .config import ResolvedAgent, TGA3Config
from .docker_runtime import ContainerRuntime, LaunchSpec
from .domain import SYSTEM_ACTOR, Actor, AgentState, EntryKind, EventKind, RunState
from .events import TaskEvents
from .storage import Storage


class HostModel(Protocol):
    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str: ...


class DeterministicHostModel:
    """Offline report model used by tests."""

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
        return f"# TGA3 Writeup\n\n## 固定黑板快照\n\n```json\n{snapshot}\n```\n"


class CliHostModel:
    """Reporter is a one-shot Codex container."""

    def __init__(self, config: TGA3Config, containers: ContainerRuntime, storage: Storage, events: TaskEvents) -> None:
        self.config, self.containers, self.storage, self.events = config, containers, storage, events

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
        entries = json.loads(snapshot)
        task_id = UUID(entries[0]["task_id"])
        binding = binding or self.config.resolve_agent("reporter")
        actor = Actor(
            agent_id="reporter",
            display_name=binding.display_name,
            role="reporter",
            sdk=binding.runtime,
            model=binding.model.name,
        )

        async def emit(method: str, params: dict) -> None:
            if method == "container.started":
                await self.storage.update_agent(task_id, "reporter", container_id=params["container_id"])
            elif method == "agent.session":
                run = await self.storage.get_agent(task_id, "reporter")
                await self.storage.update_agent(task_id, "reporter", metadata={**run.metadata, **params})
            else:
                await self.events.emit(
                    task_id,
                    actor=actor,
                    kind=EventKind.ACTION_STARTED if method.endswith("started") else EventKind.ACTION_COMPLETED,
                    text=str(params.get("summary", method)),
                    channel_agent_id="reporter",
                    payload=params,
                )

        return await self.containers.report(LaunchSpec(task_id=task_id, agent=binding, report_input=snapshot), emit)


class Automation:
    """Finalize a task once a Worker publishes a final candidate."""

    def __init__(
        self,
        config: TGA3Config,
        storage: Storage,
        blackboard: Blackboard,
        events: TaskEvents,
        model: HostModel,
        stop_workers: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self.config, self.storage, self.blackboard = config, storage, blackboard
        self.events, self.model, self.stop_workers = events, model, stop_workers
        self._reporter_jobs: dict[UUID, asyncio.Task[None]] = {}

    async def changed(self, task_id: UUID, latest_seq: int) -> None:
        task = await self.storage.get_task(task_id)
        if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return
        entries = await self.storage.list_entries(task_id, after_seq=max(0, latest_seq - 1), limit=1)
        if entries and entries[-1].kind == EntryKind.FINAL_CANDIDATE and task_id not in self._reporter_jobs:
            self._reporter_jobs[task_id] = asyncio.create_task(self._report(task_id))

    async def cancel(self, task_id: UUID) -> None:
        job = self._reporter_jobs.pop(task_id, None)
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)

    async def _report(self, task_id: UUID) -> None:
        configured = self.config.agents.agents["reporter"]
        run = await self.storage.get_agent(task_id, "reporter")
        reporter = Actor(
            agent_id="reporter",
            display_name=configured.display_name,
            role="reporter",
            sdk=configured.runtime,
            model=run.model_id,
        )
        try:
            binding = self.config.resolve_agent(
                "reporter", run.provider_id, run.model_id, run.protocol, require_api_key=False
            )
            await self.storage.update_task(task_id, state=RunState.FINALIZING)
            await asyncio.sleep(self.config.runtime.cadence.finalization_grace_seconds)
            # Both writers exit before the report snapshot is fixed.
            if self.stop_workers:
                await self.stop_workers(task_id)
            task = await self.storage.get_task(task_id)
            snapshot_seq = task.blackboard_seq
            await self.storage.update_task(task_id, state=RunState.REPORTING, final_snapshot_seq=snapshot_seq)
            await self.storage.update_agent(task_id, "reporter", actual_state=AgentState.RUNNING)
            await self.events.announce_status(task_id, reporter, AgentState.RUNNING.value)
            entries = (await self.blackboard.sync(task_id, up_to_seq=snapshot_seq, limit=10_000)).entries
            snapshot = json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2)
            markdown = await self.model.report(snapshot, binding)
            if not markdown.strip():
                raise ValueError("Reporter returned an empty report")
            root = self.config.resolve_path(self.config.runtime.writeup_root) / str(task_id)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "writeup.md"
            path.write_text(markdown, encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            await self.storage.save_writeup(task_id, snapshot_seq, str(path), digest)
        except Exception as exc:
            await self.storage.update_agent(
                task_id,
                "reporter",
                desired_state=AgentState.FAILED,
                actual_state=AgentState.FAILED,
                last_error=str(exc),
            )
            await self.storage.update_task(task_id, state=RunState.FAILED)
            await self.events.emit(
                task_id,
                actor=reporter,
                kind=EventKind.ERROR,
                text=f"Reporter 运行失败：{exc}",
                channel_agent_id="reporter",
            )
            return
        await self.storage.update_agent(
            task_id,
            "reporter",
            desired_state=AgentState.COMPLETED,
            actual_state=AgentState.COMPLETED,
        )
        await self.events.announce_status(task_id, reporter, AgentState.COMPLETED.value)
        await self.storage.update_task(task_id, state=RunState.COMPLETED)
        await self.events.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text="最终结论已固定，Markdown writeup 已生成。",
            payload={"snapshot_seq": snapshot_seq, "download_path": str(path)},
        )


__all__ = ["Automation", "CliHostModel", "DeterministicHostModel", "HostModel"]
