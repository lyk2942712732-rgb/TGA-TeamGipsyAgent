"""Host-side Supervisor advisor and final Reporter using OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .blackboard import Blackboard
from .config import ResolvedAgent, TGA3Config
from .dialogue import SolverDialogue
from .domain import SYSTEM_ACTOR, Actor, AgentState, DialogueKind, EntryKind, PublishRequest, RunState
from .skills import SkillCatalog
from .storage import Storage


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: str = Field(min_length=1)
    advice: str | None = None
    addressed_to: list[str] = Field(default_factory=list)
    question: str | None = None


class HostModel(Protocol):
    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision: ...
    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str: ...


class OpenAIHostModel:
    def __init__(self, config: TGA3Config, skills: SkillCatalog) -> None:
        self.config = config
        self.skills = skills

    @staticmethod
    def _model(binding: ResolvedAgent):
        from agents import AsyncOpenAI, OpenAIChatCompletionsModel, OpenAIResponsesModel

        client = AsyncOpenAI(
            api_key=binding.api_key.get_secret_value(),
            base_url=binding.provider.sdk_base_url(binding.protocol),
        )
        if binding.protocol == "openai_chat_completions":
            return OpenAIChatCompletionsModel(model=binding.model.name, openai_client=client)
        return OpenAIResponsesModel(model=binding.model.name, openai_client=client)

    @staticmethod
    def _supervisor_instructions(binding: ResolvedAgent) -> str:
        if binding.protocol != "openai_chat_completions":
            return binding.system_prompt
        schema = json.dumps(SupervisorDecision.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        return (
            f"{binding.system_prompt}\n\n"
            "你的最终回复必须是一个 JSON 对象，不要使用 Markdown 代码块，也不要添加 JSON 之外的文字。"
            f"JSON 必须严格符合这个 schema：{schema}"
        )

    @staticmethod
    def _parse_supervisor_decision(value: Any) -> SupervisorDecision:
        if isinstance(value, SupervisorDecision):
            return value
        if isinstance(value, dict):
            return SupervisorDecision.model_validate(value)
        text = str(value or "").strip()
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return SupervisorDecision.model_validate(payload)
        raise ValueError("Supervisor did not return a valid JSON decision")

    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision:
        from agents import Agent, Runner, function_tool, set_tracing_disabled

        set_tracing_disabled(True)
        binding = binding or self.config.resolve_agent("supervisor")

        @function_tool
        def skills_list() -> list[dict[str, Any]]:
            """List role-neutral skill packages by name and summary."""
            return [item.__dict__ for item in self.skills.list()]

        @function_tool
        def skill_read(name: str, path: str = "SKILL.md") -> str:
            """Read one package document. Start with SKILL.md and load referenced Markdown only as needed."""
            return self.skills.read(name, path)

        agent_kwargs: dict[str, Any] = {
            "name": binding.display_name,
            "model": self._model(binding),
            "tools": [skills_list, skill_read],
            "instructions": self._supervisor_instructions(binding),
        }
        if binding.protocol != "openai_chat_completions":
            agent_kwargs["output_type"] = SupervisorDecision
        agent = Agent(
            **agent_kwargs,
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return self._parse_supervisor_decision(result.final_output)

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
        from agents import Agent, Runner, function_tool, set_tracing_disabled

        set_tracing_disabled(True)
        binding = binding or self.config.resolve_agent("reporter")

        @function_tool
        def skills_list() -> list[dict[str, Any]]:
            """List role-neutral skill packages by name, including writeup guidance."""
            return [item.__dict__ for item in self.skills.list()]

        @function_tool
        def skill_read(name: str, path: str = "SKILL.md") -> str:
            """Read one package document. Start with SKILL.md and load referenced Markdown only as needed."""
            return self.skills.read(name, path)

        agent = Agent(
            name=binding.display_name,
            model=self._model(binding),
            instructions=binding.system_prompt,
            tools=[skills_list, skill_read],
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return str(result.final_output)


class DeterministicHostModel:
    """Offline test model; production selects OpenAIHostModel."""

    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision:
        return SupervisorDecision(progress="黑板已有更新，两个 Worker 正在独立推进。")

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
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

    async def cancel(self, task_id: UUID) -> None:
        jobs = [
            job
            for job in (self._supervisor_jobs.pop(task_id, None), self._reporter_jobs.pop(task_id, None))
            if job is not None and not job.done()
        ]
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._last_supervisor_seq.pop(task_id, None)
        self._last_supervisor_at.pop(task_id, None)

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
        configured = self.config.agents.agents["supervisor"]
        run = await self.storage.get_agent(task_id, "supervisor")
        binding = self.config.resolve_agent(
            "supervisor", run.provider_id, run.model_id, run.protocol, require_api_key=False
        )
        supervisor = Actor(
            agent_id="supervisor",
            display_name=configured.display_name,
            role="supervisor",
            sdk=configured.runtime,
            model=run.model_id,
        )
        await self.storage.update_agent(task_id, "supervisor", actual_state=AgentState.RUNNING)
        await self.dialogue.announce_status(task_id, supervisor, AgentState.RUNNING.value)
        try:
            sync = await self.blackboard.sync(task_id, after_seq=0)
            snapshot = json.dumps([entry.model_dump(mode="json") for entry in sync.entries], ensure_ascii=False)
            decision = await self.model.supervisor(snapshot, binding)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.storage.update_agent(
                task_id, "supervisor", actual_state=AgentState.FAILED, last_error=str(exc)
            )
            await self.dialogue.emit(
                task_id,
                actor=supervisor,
                kind=DialogueKind.ERROR,
                text=f"Supervisor 运行失败：{exc}",
            )
            return
        finally:
            current = await self.storage.get_agent(task_id, "supervisor")
            if current.actual_state != AgentState.FAILED:
                await self.storage.update_agent(task_id, "supervisor", actual_state=AgentState.IDLE)
                await self.dialogue.announce_status(task_id, supervisor, AgentState.IDLE.value)
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
        configured = self.config.agents.agents["reporter"]
        run = await self.storage.get_agent(task_id, "reporter")
        binding = self.config.resolve_agent(
            "reporter", run.provider_id, run.model_id, run.protocol, require_api_key=False
        )
        reporter = Actor(
            agent_id="reporter",
            display_name=configured.display_name,
            role="reporter",
            sdk=configured.runtime,
            model=run.model_id,
        )
        await self.storage.update_agent(task_id, "reporter", actual_state=AgentState.RUNNING)
        await self.dialogue.announce_status(task_id, reporter, AgentState.RUNNING.value)
        entries = await self.storage.list_entries(task_id, up_to_seq=snapshot_seq, limit=10_000)
        snapshot = json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2)
        try:
            markdown = await self.model.report(snapshot, binding)
        except Exception as exc:
            await self.storage.update_agent(task_id, "reporter", actual_state=AgentState.FAILED, last_error=str(exc))
            await self.storage.update_task(task_id, state=RunState.FAILED)
            await self.dialogue.emit(
                task_id,
                actor=reporter,
                kind=DialogueKind.ERROR,
                text=f"Reporter 运行失败：{exc}",
                channel_agent_id="reporter",
            )
            return
        root = self.config.resolve_path(self.config.runtime.writeup_root) / str(task_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "writeup.md"
        path.write_text(markdown, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        await self.storage.save_writeup(task_id, snapshot_seq, str(path), digest)
        if self.on_reported:
            await self.on_reported(task_id)
        await self.storage.update_agent(
            task_id,
            "reporter",
            desired_state=AgentState.COMPLETED,
            actual_state=AgentState.COMPLETED,
        )
        await self.storage.update_agent(
            task_id,
            "supervisor",
            desired_state=AgentState.COMPLETED,
            actual_state=AgentState.COMPLETED,
        )
        await self.dialogue.announce_status(task_id, reporter, AgentState.COMPLETED.value)
        await self.storage.update_task(task_id, state=RunState.COMPLETED)
        await self.dialogue.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.SYSTEM,
            text="最终结论已固定，Markdown writeup 已生成。",
            payload={"snapshot_seq": snapshot_seq, "download_path": str(path)},
        )


__all__ = ["Automation", "DeterministicHostModel", "OpenAIHostModel", "SupervisorDecision"]
