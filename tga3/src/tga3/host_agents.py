"""Host-side Supervisor advisor and final Reporter using OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .blackboard import Blackboard
from .config import ResolvedAgent, TGA3Config
from .dialogue import SolverDialogue
from .domain import SYSTEM_ACTOR, Actor, DialogueKind, EntryKind, PublishRequest, RunState
from .skills import SkillCatalog
from .storage import Storage


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: str = Field(min_length=1)
    advice: str | None = None
    addressed_to: list[str] = Field(default_factory=list)
    question: str | None = None


class HostModel(Protocol):
    async def supervisor(self, snapshot: str) -> SupervisorDecision: ...
    async def report(self, snapshot: str) -> str: ...


class OpenAIHostModel:
    def __init__(self, config: TGA3Config, skills: SkillCatalog) -> None:
        self.config = config
        self.skills = skills

    @staticmethod
    def _model(binding: ResolvedAgent):
        from agents import AsyncOpenAI, OpenAIChatCompletionsModel, OpenAIResponsesModel

        client = AsyncOpenAI(
            api_key=binding.api_key.get_secret_value(),
            base_url=binding.provider.base_url,
        )
        if binding.provider.protocol == "openai_chat_completions":
            return OpenAIChatCompletionsModel(model=binding.model.name, openai_client=client)
        return OpenAIResponsesModel(model=binding.model.name, openai_client=client)

    async def supervisor(self, snapshot: str) -> SupervisorDecision:
        from agents import Agent, Runner, function_tool, set_tracing_disabled

        set_tracing_disabled(True)
        binding = self.config.resolve_agent("supervisor")

        @function_tool
        def skills_list() -> list[dict[str, str]]:
            """List role-neutral skills available by name."""
            return [item.__dict__ for item in self.skills.list()]

        @function_tool
        def skill_read(name: str) -> str:
            """Read one selected skill completely."""
            return self.skills.read(name)

        agent = Agent(
            name="Supervisor",
            model=self._model(binding),
            output_type=SupervisorDecision,
            tools=[skills_list, skill_read],
            instructions=(
                "You are only an advisor. Read the compact blackboard, optionally load a skill by name, "
                "describe observable progress, and give concise useful advice to either or both workers. "
                "Do not schedule, verify artifacts, execute tools, or claim work was done. Ask a user question "
                "only when the shared task truly cannot progress without user input. "
                "Do not reveal hidden chain-of-thought."
            ),
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return SupervisorDecision.model_validate(result.final_output)

    async def report(self, snapshot: str) -> str:
        from agents import Agent, Runner, set_tracing_disabled

        set_tracing_disabled(True)
        binding = self.config.resolve_agent("reporter")
        agent = Agent(
            name="Reporter",
            model=self._model(binding),
            instructions=(
                "Write a standalone Markdown writeup from the fixed blackboard snapshot. Explain the approach, "
                "verified findings and final result. Do not invent evidence or mention hidden artifact links. "
                "Return Markdown only."
            ),
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return str(result.final_output)


class DeterministicHostModel:
    """Offline test model; production selects OpenAIHostModel."""

    async def supervisor(self, snapshot: str) -> SupervisorDecision:
        return SupervisorDecision(progress="黑板已有更新，两个 Worker 正在独立推进。")

    async def report(self, snapshot: str) -> str:
        return f"# TGA3 Writeup\n\n## 固定黑板快照\n\n```json\n{snapshot}\n```\n"


class Automation:
    """Debounced advisor plus one-shot reporter. No graph scheduler is involved."""

    def __init__(
        self,
        config: TGA3Config,
        storage: Storage,
        blackboard: Blackboard,
        dialogue: SolverDialogue,
        model: HostModel,
        on_reported: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.blackboard = blackboard
        self.dialogue = dialogue
        self.model = model
        self.on_reported = on_reported
        self._supervisor_jobs: dict[UUID, asyncio.Task[None]] = {}
        self._reporter_jobs: dict[UUID, asyncio.Task[None]] = {}
        self._last_supervisor_seq: dict[UUID, int] = {}
        self._last_supervisor_at: dict[UUID, float] = {}

    async def changed(self, task_id: UUID, latest_seq: int) -> None:
        entries = await self.storage.list_entries(task_id, after_seq=max(0, latest_seq - 1), limit=1)
        if not entries:
            return
        latest = entries[-1]
        if latest.kind == EntryKind.FINAL_CANDIDATE and task_id not in self._reporter_jobs:
            self._reporter_jobs[task_id] = asyncio.create_task(self._report(task_id))
        if latest.kind == EntryKind.SUPERVISOR_ADVICE:
            return
        old = self._supervisor_jobs.get(task_id)
        if old and not old.done():
            old.cancel()
        self._supervisor_jobs[task_id] = asyncio.create_task(self._advise(task_id, latest_seq))

    async def _advise(self, task_id: UUID, trigger_seq: int) -> None:
        await asyncio.sleep(self.config.runtime.cadence.supervisor_debounce_seconds)
        loop = asyncio.get_running_loop()
        remaining = (
            self._last_supervisor_at.get(task_id, 0)
            + self.config.runtime.cadence.supervisor_cooldown_seconds
            - loop.time()
        )
        if remaining > 0:
            await asyncio.sleep(remaining)
        last = self._last_supervisor_seq.get(task_id, 0)
        if trigger_seq <= last:
            return
        sync = await self.blackboard.sync(task_id, after_seq=0)
        snapshot = json.dumps([entry.model_dump(mode="json") for entry in sync.entries], ensure_ascii=False)
        decision = await self.model.supervisor(snapshot)
        supervisor = Actor(
            agent_id="supervisor",
            display_name="Supervisor",
            role="supervisor",
            sdk=self.config.agent_models.agents["supervisor"].runtime,
            model=self.config.agent_models.agents["supervisor"].model_id,
        )
        await self.dialogue.announce_blackboard(task_id, sync.latest_seq, decision.progress)
        if decision.advice:
            await self.blackboard.publish(
                task_id,
                supervisor,
                PublishRequest(
                    kind=EntryKind.SUPERVISOR_ADVICE,
                    topic="advice",
                    body={
                        "advice": decision.advice,
                        "addressed_to": decision.addressed_to,
                        "based_on_seq": sync.latest_seq,
                    },
                    idempotency_key=f"advice-{sync.latest_seq}",
                ),
            )
        if decision.question:
            await self.dialogue.ask(task_id, supervisor=supervisor, question=decision.question)
            await self.storage.update_task(task_id, state=RunState.WAITING_USER)
        self._last_supervisor_seq[task_id] = sync.latest_seq
        self._last_supervisor_at[task_id] = loop.time()

    async def _report(self, task_id: UUID) -> None:
        await self.storage.update_task(task_id, state=RunState.FINALIZING)
        await asyncio.sleep(self.config.runtime.cadence.finalization_grace_seconds)
        task = await self.storage.get_task(task_id)
        snapshot_seq = task.blackboard_seq
        await self.storage.update_task(task_id, state=RunState.REPORTING, final_snapshot_seq=snapshot_seq)
        entries = await self.storage.list_entries(task_id, up_to_seq=snapshot_seq, limit=10_000)
        snapshot = json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2)
        markdown = await self.model.report(snapshot)
        root = self.config.resolve_path(self.config.runtime.writeup_root) / str(task_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "writeup.md"
        path.write_text(markdown, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        await self.storage.save_writeup(task_id, snapshot_seq, str(path), digest)
        if self.on_reported:
            await self.on_reported(task_id)
        await self.storage.update_task(task_id, state=RunState.COMPLETED)
        await self.dialogue.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.SYSTEM,
            text="最终结论已固定，Markdown writeup 已生成。",
            payload={"snapshot_seq": snapshot_seq, "download_path": str(path)},
        )


__all__ = ["Automation", "DeterministicHostModel", "OpenAIHostModel", "SupervisorDecision"]
